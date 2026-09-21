"""Train a small ONNX wake-word network and export identical weights for ESP32.

By default, splits entire recording sessions BEFORE extracting windows. The opt-in
recordings mode is exploratory and can share a session across splits. Never uses
test data to choose the threshold. A baseline is not a production quality claim.
"""
import argparse
import ctypes
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ferris.audio import read_recording
from ferris.core import UserError
from ferris.detector import Features
from tools.training import augment
from tools.training.evaluation import stream_windows, confirmation_score, choose_threshold
from tools.training.build_dsp import build


def windows(pcm, positive):
    import numpy as np
    audio = np.frombuffer(pcm, dtype='<i2').copy()
    if len(audio) < 16000:
        audio = np.pad(audio, (0, 16000-len(audio)))
    if positive:
        # Centre speech energy, excluding the microphone DC offset.
        power = (audio.astype(float) - audio.mean()) ** 2
        centre = int(np.dot(np.arange(len(audio)), power) / max(power.sum(), 1e-12))
        start = max(0, min(len(audio)-16000, centre-8000))
        centred = audio[start:start+16000].astype('<i2').tobytes()
        # Train the same overlapping positions used during live detection.
        # Keep only windows containing most of the recording's speech energy.
        clips = [centred]
        for clip in stream_windows(pcm):
            segment = np.frombuffer(clip, dtype='<i2').astype(float)
            if np.sum((segment-audio.mean())**2) >= .90 * power.sum() and clip not in clips:
                clips.append(clip)
        return clips
    return stream_windows(pcm)


def split_sessions(records):
    import numpy as np
    from sklearn.model_selection import GroupShuffleSplit
    y = np.array([r['label'] for r in records]); groups = np.array([r['group'] for r in records])
    if len(set(groups)) < 4:
        raise ValueError('Grave pelo menos 4 sessões diferentes, com Ferris e negativos em cada uma.')
    best = None
    total_pos = int((y == 1).sum())
    for seed in range(100):
        trainval, test = next(GroupShuffleSplit(n_splits=1, test_size=.2, random_state=seed).split(y, y, groups))
        a, b = next(GroupShuffleSplit(n_splits=1, test_size=.25, random_state=seed).split(y[trainval], y[trainval], groups[trainval]))
        train, val = trainval[a], trainval[b]
        if not all(len(set(y[ids])) == 2 for ids in (train, val, test)):
            continue
        windows = {}
        ok = True
        for name, ids in (('train', train), ('validation', val), ('test', test)):
            pos = ids[y[ids] == 1]
            windows[name] = int((y[ids] == 1).sum())
            if len(pos) == 0:
                ok = False
                break
        if not ok:
            continue
        if windows['train'] < total_pos//2:
            continue
        key = (windows['validation'], windows['train'])
        if best is None or key > best[0]:
            best = (key, {'train': train, 'validation': val, 'test': test})
    if best is None:
        raise ValueError('Não foi possível separar sessões mantendo positivos e negativos em todos os conjuntos.')
    return best[1]


def split_recordings(records):
    """Exploratory holdout: whole files, with each original class represented."""
    import numpy as np
    from sklearn.model_selection import train_test_split
    ids = np.arange(len(records))
    classes = np.array([r['category'] for r in records])
    trainval, test = train_test_split(ids, test_size=.2, random_state=42, stratify=classes)
    train, val = train_test_split(trainval, test_size=.25, random_state=42, stratify=classes[trainval])
    return {'train': train, 'validation': val, 'test': test}


def is_personal(record):
    return Path(record['path']).name.startswith(('esp32-', 'pc-'))


def select_training(records, ids, limit=1200):
    """Keep every personal recording and positive; bound imported negatives only."""
    import numpy as np
    rng = np.random.default_rng(42)
    selected = [i for i in ids if records[i]['label'] or is_personal(records[i])]
    for category in ('other', 'noise'):
        imported = [i for i in ids if not is_personal(records[i]) and records[i]['category'] == category]
        selected.extend(rng.choice(imported, min(limit, len(imported)), replace=False).tolist())
    return sorted(selected)


def training_weights(records, spans):
    """Give each recording equal weight within its class and microphone source."""
    import numpy as np
    weights = np.zeros(spans[-1][1])
    for label in (0, 1):
        buckets = [[(a,b) for a,b,i in spans if records[i]['label'] == label
                    and is_personal(records[i]) == personal] for personal in (False, True)]
        shares = (.3, .7) if all(buckets) else (1., 1.)
        for bucket, share in zip(buckets, shares):
            for a,b in bucket:
                weights[a:b] = share / len(bucket) / (b-a)
    return weights / weights.mean()


def report(y, probabilities, threshold):
    import numpy as np
    predicted = probabilities >= threshold; y = np.asarray(y)
    tp = int(((y == 1) & predicted).sum()); fp = int(((y == 0) & predicted).sum())
    tn = int(((y == 0) & ~predicted).sum()); fn = int(((y == 1) & ~predicted).sum())
    return dict(tp=tp, fp=fp, tn=tn, fn=fn, recall=tp/max(1,tp+fn),
                false_positive_rate=fp/max(1,fp+tn), accuracy=(tp+tn)/len(y))


def train(data, output, header=None, split_mode='sessions'):
    import numpy as np
    import onnx
    import onnxruntime as ort
    from onnx import TensorProto, helper, numpy_helper
    from sklearn.neural_network import MLPClassifier
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
            records.append(dict(path=path, pcm=pcm, group=path.parent.name, label=int(label == 'ferris'), category=label, sha256=digest))
    if sum(r['label'] for r in records) < 12 or sum(not r['label'] for r in records) < 12:
        raise ValueError('São necessários pelo menos 12 exemplos Ferris e 12 negativos. Para qualidade, colete muito mais.')
    if split_mode == 'sessions':
        splits = split_sessions(records)
    elif split_mode == 'recordings':
        print('Avaliação exploratória por gravação: as sessões podem se repetir entre conjuntos. '
              'Valide em novas sessões antes de usar como resultado final.', file=sys.stderr)
        splits = split_recordings(records)
    else:
        raise ValueError('Modo de separação inválido: use sessions ou recordings.')
    frontend = Features(build()); arrays = {}; stream = {}
    training_ids = select_training(records, splits['train'])
    print(f'Treino: {len(training_ids)} originais selecionados; avaliação usa todos os arquivos reservados.', flush=True)
    rng = np.random.default_rng(42)
    # O ambiente usado nas misturas sai só do treino. Pegar ruído de validação ou
    # de teste colocaria áudio desses conjuntos dentro do modelo.
    ambient = [records[i]['pcm'] for i in training_ids if records[i]['category'] == 'noise']
    added = {'ferris': 0, 'other': 0, 'noise': 0, 'partial_word': 0}
    for name, ids in splits.items():
        xs, ys, spans = [], [], []
        used = training_ids if name == 'train' else ids
        for i in used:
            r = records[i]
            start = len(xs)
            clips = windows(r['pcm'], r['label']) if name == 'train' else stream_windows(r['pcm'])
            for pcm in clips:
                xs.append(frontend(pcm)); ys.append(r['label'])
                if name != 'train':
                    continue  # validação e teste ficam com o áudio original
                if r['label']:
                    extras = [(item, 1) for item in augment.positive_variants(pcm, rng, ambient)]
                elif r['category'] == 'other':
                    extras = [(item, 0) for item in augment.speech_negative_variants(pcm, rng, ambient)]
                else:
                    extras = [(item, 0) for item in augment.ambient_variants(pcm, rng, ambient)]
                added[r['category']] += len(extras)
                for item, label in extras:
                    xs.append(frontend(item)); ys.append(label)
            spans.append((start, len(xs), int(i)))
        arrays[name] = (np.asarray(xs, dtype=np.float32), np.asarray(ys))
        stream[name] = spans
        print(f'{name}: {len(xs)} janelas.', flush=True)
    x, y = arrays['train']; scaler = StandardScaler().fit(x)
    clf = MLPClassifier(hidden_layer_sizes=(24,), activation='relu', batch_size=256,
                        max_iter=200, early_stopping=True, n_iter_no_change=15,
                        random_state=42).fit(scaler.transform(x), y,
                                             sample_weight=training_weights(records, stream['train']))
    print(f'Rede: {clf.n_iter_} épocas.', flush=True)
    # Fold the scaler into the first layer; both runtimes consume raw features.
    weights = (clf.coefs_[0] / scaler.scale_[:, None]).astype(np.float32)
    hidden_bias = (clf.intercepts_[0] - (scaler.mean_ / scaler.scale_) @ clf.coefs_[0]).astype(np.float32)
    output_weights = clf.coefs_[1].astype(np.float32)
    bias = np.float32(clf.intercepts_[1][0])
    vx, vy = arrays['validation']; vp = clf.predict_proba(scaler.transform(vx))[:,1]
    def evaluate_clips(name, probabilities):
        spans = stream[name]
        scores = np.array([confirmation_score(probabilities[a:b]) for a,b,_ in spans])
        labels = np.array([records[i]['label'] for _,_,i in spans])
        return labels, scores
    validation_labels, validation_scores = evaluate_clips('validation', vp)
    personal_mask = np.array([is_personal(records[i]) for _,_,i in stream['validation']])
    threshold = choose_threshold(validation_labels, validation_scores, personal=personal_mask)
    graph = helper.make_graph([
        helper.make_node('MatMul', ['features', 'weights'], ['linear']),
        helper.make_node('Add', ['linear', 'hidden_bias'], ['hidden_linear']),
        helper.make_node('Relu', ['hidden_linear'], ['hidden']),
        helper.make_node('MatMul', ['hidden', 'output_weights'], ['output_linear']),
        helper.make_node('Add', ['output_linear', 'bias'], ['logits']),
        helper.make_node('Sigmoid', ['logits'], ['probability'])], 'FerrisKeyword',
        [helper.make_tensor_value_info('features', TensorProto.FLOAT, [None, 150])],
        [helper.make_tensor_value_info('probability', TensorProto.FLOAT, [None, 1])],
        [numpy_helper.from_array(weights, 'weights'), numpy_helper.from_array(hidden_bias, 'hidden_bias'),
         numpy_helper.from_array(output_weights, 'output_weights'), numpy_helper.from_array(np.array([bias]), 'bias')])
    model = helper.make_model(graph, producer_name='ferris', opset_imports=[helper.make_opsetid('', 17)])
    model.ir_version = 9; onnx.checker.check_model(model)
    onnx.save(model, output/'wake.onnx')
    session = ort.InferenceSession(str(output/'wake.onnx'), providers=['CPUExecutionProvider'])
    tx, ty = arrays['test']; probs = session.run(None, {'features': tx})[0].ravel()
    cweights = (ctypes.c_float*3600)(*weights.ravel())
    chidden = (ctypes.c_float*24)(*hidden_bias)
    coutput = (ctypes.c_float*24)(*output_weights.ravel())
    cp = np.array([frontend.lib.ferris_predict_hidden((ctypes.c_float*150)(*row), cweights, chidden, coutput, float(bias)) for row in tx])
    error = float(np.max(np.abs(probs-cp)))
    if error > 1e-4:
        raise ValueError(f'Inferência C divergiu do ONNX: {error}')
    test_labels, test_scores = evaluate_clips('test', probs)
    def personal_report(name, labels, scores):
        mask = np.array([is_personal(records[i]) for _,_,i in stream[name]])
        return report(labels[mask], scores[mask], threshold) if mask.any() else None
    metadata = dict(format_version=1, sample_rate=16000, samples=16000, features=150,
                    threshold=threshold, architecture='dense_150_24_1', weights=weights.tolist(),
                    hidden_bias=hidden_bias.tolist(), output_weights=output_weights.tolist(), bias=float(bias),
                    dsp_sha256=hashlib.sha256((ROOT/'firmware/components/ferris_dsp/ferris_dsp.c').read_bytes()).hexdigest(),
                    model_sha256=hashlib.sha256((output/'wake.onnx').read_bytes()).hexdigest(),
                    validation=report(validation_labels, validation_scores, threshold),
                    test=report(test_labels, test_scores, threshold),
                    personal_validation=personal_report('validation', validation_labels, validation_scores),
                    personal_test=personal_report('test', test_labels, test_scores),
                    onnx_c_max_error=error, split_mode=split_mode, metric_unit='recording_two_consecutive_windows',
                    selected_training_recordings=[records[i]['sha256'] for i in training_ids],
                    augmentation=dict(train_only=True, added_windows=added),
                    splits={name: sorted({records[i]['group'] for i in ids}) for name, ids in splits.items()},
                    split_recordings={name: [records[i]['sha256'] for i in ids] for name, ids in splits.items()},
                    recordings=[{k: str(v) if k == 'path' else v for k,v in r.items() if k != 'pcm'} for r in records],
                    limitations=('Avaliação exploratória por arquivos; não mede generalização entre sessões. '
                                 if split_mode == 'recordings' else '')
                    +'Rede compacta; validar falsos acionamentos/hora em áudio contínuo real. Não é identificação biométrica.')
    (output/'wake.json').write_text(json.dumps(metadata, indent=2, ensure_ascii=False)+'\n')
    if header:
        header = Path(header); header.parent.mkdir(parents=True, exist_ok=True)
        def cfloat(value):
            return f'{float(value):.9e}f'
        header.write_text('#pragma once\n#define FERRIS_MODEL_READY 1\n#define FERRIS_MODEL_HIDDEN 24\n'
                          +f'#define FERRIS_THRESHOLD {cfloat(threshold)}\n'
                          +'static const float ferris_weights[3600] = {'+','.join(map(cfloat,weights.ravel()))+'};\n'
                          +'static const float ferris_hidden_bias[24] = {'+','.join(map(cfloat,hidden_bias))+'};\n'
                          +'static const float ferris_output_weights[24] = {'+','.join(map(cfloat,output_weights.ravel()))+'};\n'
                          +f'static const float ferris_bias = {cfloat(bias)};\n')
    print(json.dumps({k: metadata[k] for k in ('split_mode','threshold','validation','test','onnx_c_max_error','personal_validation','personal_test')}, indent=2))
    return metadata


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', type=Path, default=ROOT/'data/recordings')
    p.add_argument('--output', type=Path, default=ROOT/'models')
    p.add_argument('--header', type=Path, default=ROOT/'firmware/main/model_weights.h')
    p.add_argument('--split-mode', choices=('sessions', 'recordings'), default='sessions',
                   help='sessions: avaliação entre sessões; recordings: versão experimental por arquivos inteiros')
    a = p.parse_args()
    try: train(a.data, a.output, a.header, split_mode=a.split_mode)
    except (ValueError, RuntimeError) as e: p.exit(1, str(e)+'\n')
