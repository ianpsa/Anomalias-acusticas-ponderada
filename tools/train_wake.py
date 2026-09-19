"""Train an ONNX wake-word baseline and export identical affine weights for ESP32.

Splits entire recording sessions BEFORE extracting windows. Never uses test data
to choose the threshold. A baseline is not a claim of production wake-word quality.
"""
import argparse
import ctypes
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ferris.audio import read_recording
from ferris.core import UserError
from ferris.detector import Features
from tools.build_dsp import build


def windows(pcm, positive):
    import numpy as np
    audio = np.frombuffer(pcm, dtype='<i2').copy()
    if len(audio) < 16000:
        audio = np.pad(audio, (0, 16000-len(audio)))
    if positive:
        # The user records one complete word near the center. Select the strongest
        # one-second region, keeping the entire keyword; jitter only in training.
        starts = list(range(0, len(audio)-15999, 800))
        start = max(starts, key=lambda n: np.square(audio[n:n+16000].astype(float)).sum())
        return [audio[start:start+16000].astype('<i2').tobytes()]
    return [audio[i:i+16000].astype('<i2').tobytes() for i in range(0, len(audio)-15999, 8000)]


def split_sessions(records):
    import numpy as np
    from sklearn.model_selection import GroupShuffleSplit
    y = np.array([r['label'] for r in records]); groups = np.array([r['group'] for r in records])
    if len(set(groups)) < 4:
        raise ValueError('Grave pelo menos 4 sessões diferentes, com Ferris e negativos em cada uma.')
    for seed in range(100):
        trainval, test = next(GroupShuffleSplit(n_splits=1, test_size=.2, random_state=seed).split(y, y, groups))
        a, b = next(GroupShuffleSplit(n_splits=1, test_size=.25, random_state=seed).split(y[trainval], y[trainval], groups[trainval]))
        train, val = trainval[a], trainval[b]
        if all(len(set(y[ids])) == 2 for ids in (train, val, test)):
            return {'train': train, 'validation': val, 'test': test}
    raise ValueError('Não foi possível separar sessões mantendo positivos e negativos em todos os conjuntos.')


def report(y, probabilities, threshold):
    import numpy as np
    predicted = probabilities >= threshold; y = np.asarray(y)
    tp = int(((y == 1) & predicted).sum()); fp = int(((y == 0) & predicted).sum())
    tn = int(((y == 0) & ~predicted).sum()); fn = int(((y == 1) & ~predicted).sum())
    return dict(tp=tp, fp=fp, tn=tn, fn=fn, recall=tp/max(1,tp+fn),
                false_positive_rate=fp/max(1,fp+tn), accuracy=(tp+tn)/len(y))


def train(data, output, header=None):
    import numpy as np
    import onnx
    import onnxruntime as ort
    from onnx import TensorProto, helper, numpy_helper
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    records, seen = [], set()
    for label in ('ferris', 'other', 'noise'):
        for path in sorted((Path(data)/label).glob('*/*.wav')):
            raw = path.read_bytes()
            try:
                pcm = read_recording(raw)
            except UserError as exc:
                raise ValueError(f'{path}: {exc}') from exc
            digest = hashlib.sha256(pcm).hexdigest()
            if digest in seen:
                raise ValueError(f'Áudio duplicado: {path}. Remova duplicatas antes de avaliar.')
            seen.add(digest)
            records.append(dict(path=path, pcm=pcm, group=path.parent.name, label=int(label == 'ferris'), sha256=digest))
    if sum(r['label'] for r in records) < 12 or sum(not r['label'] for r in records) < 12:
        raise ValueError('São necessários pelo menos 12 exemplos Ferris e 12 negativos. Para qualidade, colete muito mais.')
    splits = split_sessions(records)
    frontend = Features(build()); arrays = {}
    for name, ids in splits.items():
        xs, ys = [], []
        for i in ids:
            r = records[i]
            for pcm in windows(r['pcm'], r['label']):
                xs.append(frontend(pcm)); ys.append(r['label'])
                if name == 'train' and r['label']:
                    a = np.frombuffer(pcm, dtype='<i2')
                    for offset in (-1600, 1600):
                        shifted = np.zeros(16000, dtype='<i2')
                        if offset > 0: shifted[offset:] = a[:-offset]
                        else: shifted[:offset] = a[-offset:]
                        xs.append(frontend(shifted.tobytes())); ys.append(1)
        arrays[name] = (np.asarray(xs, dtype=np.float32), np.asarray(ys))
    x, y = arrays['train']; scaler = StandardScaler().fit(x)
    clf = LogisticRegression(C=.5, class_weight='balanced', max_iter=2000, random_state=42).fit(scaler.transform(x), y)
    # Fold scaler into weights. C and ONNX both consume raw DSP features.
    weights = (clf.coef_[0] / scaler.scale_).astype(np.float32)
    bias = np.float32(clf.intercept_[0] - np.dot(clf.coef_[0], scaler.mean_ / scaler.scale_))
    vx, vy = arrays['validation']; vp = clf.predict_proba(scaler.transform(vx))[:,1]
    candidates = []
    for threshold in np.linspace(.5, .99, 50):
        metrics = report(vy, vp, threshold)
        candidates.append((metrics['recall'] < .8, metrics['false_positive_rate'], -metrics['recall'], float(threshold)))
    threshold = min(candidates)[-1]
    graph = helper.make_graph([
        helper.make_node('MatMul', ['features', 'weights'], ['linear']),
        helper.make_node('Add', ['linear', 'bias'], ['logits']),
        helper.make_node('Sigmoid', ['logits'], ['probability'])], 'FerrisKeyword',
        [helper.make_tensor_value_info('features', TensorProto.FLOAT, [None, 150])],
        [helper.make_tensor_value_info('probability', TensorProto.FLOAT, [None, 1])],
        [numpy_helper.from_array(weights.reshape(150,1), 'weights'), numpy_helper.from_array(np.array([bias]), 'bias')])
    model = helper.make_model(graph, producer_name='ferris', opset_imports=[helper.make_opsetid('', 17)])
    model.ir_version = 9; onnx.checker.check_model(model)
    onnx.save(model, output/'wake.onnx')
    session = ort.InferenceSession(str(output/'wake.onnx'), providers=['CPUExecutionProvider'])
    tx, ty = arrays['test']; probs = session.run(None, {'features': tx})[0].ravel()
    cweights = (ctypes.c_float*150)(*weights)
    cp = np.array([frontend.lib.ferris_predict((ctypes.c_float*150)(*row), cweights, float(bias)) for row in tx])
    error = float(np.max(np.abs(probs-cp)))
    if error > 1e-4:
        raise ValueError(f'Inferência C divergiu do ONNX: {error}')
    metadata = dict(format_version=1, sample_rate=16000, samples=16000, features=150,
                    threshold=threshold, weights=weights.tolist(), bias=float(bias),
                    dsp_sha256=hashlib.sha256((ROOT/'firmware/components/ferris_dsp/ferris_dsp.c').read_bytes()).hexdigest(),
                    model_sha256=hashlib.sha256((output/'wake.onnx').read_bytes()).hexdigest(),
                    validation=report(vy, vp, threshold), test=report(ty, probs, threshold),
                    onnx_c_max_error=error,
                    splits={name: sorted({records[i]['group'] for i in ids}) for name, ids in splits.items()},
                    recordings=[{k: str(v) if k == 'path' else v for k,v in r.items() if k != 'pcm'} for r in records],
                    limitations='Baseline linear; validar falsos acionamentos/hora em áudio contínuo real. Não é identificação biométrica.')
    (output/'wake.json').write_text(json.dumps(metadata, indent=2, ensure_ascii=False)+'\n')
    if header:
        header = Path(header); header.parent.mkdir(parents=True, exist_ok=True)
        def cfloat(value):
            return f'{float(value):.9e}f'
        header.write_text('#pragma once\n#define FERRIS_MODEL_READY 1\n'
                          +f'#define FERRIS_THRESHOLD {cfloat(threshold)}\n'
                          +'static const float ferris_weights[150] = {'+','.join(map(cfloat,weights))+'};\n'
                          +f'static const float ferris_bias = {cfloat(bias)};\n')
    print(json.dumps({k: metadata[k] for k in ('threshold','validation','test','onnx_c_max_error','splits')}, indent=2))
    return metadata


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', type=Path, default=ROOT/'data/recordings')
    p.add_argument('--output', type=Path, default=ROOT/'models')
    p.add_argument('--header', type=Path, default=ROOT/'firmware/main/model_weights.h')
    a = p.parse_args()
    try: train(a.data, a.output, a.header)
    except (ValueError, RuntimeError) as e: p.exit(1, str(e)+'\n')
