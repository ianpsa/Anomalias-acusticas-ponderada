"""Replay a labeled continuous WAV; report real host latency and activation metrics."""
import argparse
import json
import sys
import time
import wave
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    import numpy as np
    import onnxruntime as ort
    from ferris.detector import Features
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('wav', type=Path)
    p.add_argument('--events', type=Path, help='JSON list of expected keyword end times in seconds; [] for negative-only audio')
    p.add_argument('--model', type=Path, default=ROOT/'models')
    p.add_argument('--output', type=Path, default=ROOT/'data/benchmark.json')
    a = p.parse_args()
    with wave.open(str(a.wav)) as f:
        if (f.getnchannels(),f.getsampwidth(),f.getframerate()) != (1,2,16000): p.error('WAV PCM mono 16 bits 16 kHz obrigatório')
        pcm = f.readframes(f.getnframes())
    if len(pcm)<32000: p.error('Áudio deve ter ao menos 1 segundo')
    metadata = json.loads((a.model/'wake.json').read_text())
    model = ort.InferenceSession(str(a.model/'wake.onnx'), providers=['CPUExecutionProvider'])
    feature = Features(); features_ms=[]; infer_ms=[]; detected=[]; hits=0; cooldown=0
    for offset in range(0,len(pcm)-31999,8000):
        start=time.perf_counter(); x=np.asarray([feature(pcm[offset:offset+32000])],dtype=np.float32)
        mid=time.perf_counter(); confidence=float(model.run(None,{'features':x})[0][0][0]); end=time.perf_counter()
        features_ms.append((mid-start)*1000); infer_ms.append((end-mid)*1000)
        timestamp=offset/32000+1
        hits=hits+1 if confidence>=metadata['threshold'] else 0
        if hits>=2 and timestamp>=cooldown:
            detected.append(timestamp); cooldown=timestamp+3; hits=0
    result=dict(platform='host CPU; not ESP32', duration_s=len(pcm)/32000, detections_s=detected,
                features_ms={f'p{q}':float(np.percentile(features_ms,q)) for q in (50,95,99)},
                inference_ms={f'p{q}':float(np.percentile(infer_ms,q)) for q in (50,95,99)})
    if a.events:
        expected=json.loads(a.events.read_text()); unmatched=list(expected); false=0; delays=[]
        for t in detected:
            matches=[v for v in unmatched if 0<=t-v<=1.5]
            if matches:
                v=min(matches,key=lambda v:abs(v-t)); unmatched.remove(v); delays.append(t-v)
            else: false+=1
        result.update(expected=len(expected),missed=len(unmatched),false_activations=false,
                      false_activations_per_hour=false/(len(pcm)/32000/3600),activation_delay_s=delays)
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()
