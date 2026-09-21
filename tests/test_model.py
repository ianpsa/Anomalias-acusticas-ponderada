import ctypes
import importlib.util
import io
import json
import math
import tempfile
import unittest
import wave
from pathlib import Path
from ferris.detector import Features, Detector
from tools.training.build_dsp import build


class DSPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.features=Features(build())

    def test_silence_finite_and_reproducible(self):
        result=self.features(b'\0'*32000)
        self.assertEqual(len(result),150)
        self.assertTrue(all(math.isfinite(x) for x in result))
        self.assertEqual(result,self.features(b'\0'*32000))
        self.assertEqual(result[0],0); self.assertEqual(result[1],0)

    def test_sine_rms_and_centroid(self):
        import array
        a=array.array('h',[int(10000*math.sin(2*math.pi*1000*i/16000)) for i in range(16000)])
        result=self.features(a.tobytes())
        self.assertAlmostEqual(result[0],10000/32768/math.sqrt(2),places=3)
        self.assertAlmostEqual(result[1],1000/8000,places=3)


@unittest.skipUnless(importlib.util.find_spec('onnxruntime') and importlib.util.find_spec('sklearn'),'Install .[train]')
class ONNXTests(unittest.TestCase):
    def test_exploratory_split_keeps_files_whole_and_all_classes(self):
        from tools.training.train_wake import split_recordings, split_sessions
        records=[{'group':'one-session','category':category,'label':int(category=='ferris')}
                 for category in ('ferris','other','noise') for _ in range(20)]
        with self.assertRaisesRegex(ValueError,'4 sessões'):
            split_sessions(records)
        splits=split_recordings(records)
        all_ids=[]
        for name,ids in splits.items():
            self.assertEqual({records[i]['category'] for i in ids},{'ferris','other','noise'})
            self.assertEqual(list(ids),list(split_recordings(records)[name]))
            all_ids.extend(ids)
        self.assertEqual(sorted(all_ids),list(range(len(records))))

    def test_synthetic_pipeline_export_split_and_runtime_parity(self):
        # Synthetic tones only test plumbing. Never report these as wake-word accuracy.
        import numpy as np
        import onnx
        from tools.training.train_wake import train
        rng=np.random.default_rng(5)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); data=root/'recordings'
            for group in range(6):
                for label in ('ferris','other'):
                    folder=data/label/f'session{group}'; folder.mkdir(parents=True)
                    for i in range(4):
                        t=np.arange(32000)/16000
                        hz=(900 if label=='ferris' else 2500)+rng.uniform(-40,40)
                        audio=(np.sin(2*np.pi*hz*t)*8000+rng.normal(0,100,32000)).astype('<i2')
                        with wave.open(str(folder/f'{i}.wav'),'wb') as f:
                            f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000); f.writeframes(audio.tobytes())
            output=root/'model'; metadata=train(data,output,root/'weights.h')
            onnx.checker.check_model(onnx.load(output/'wake.onnx'))
            self.assertLess(metadata['onnx_c_max_error'],1e-4)
            self.assertEqual(metadata['split_mode'],'sessions')
            recording_ids=[digest for ids in metadata['split_recordings'].values() for digest in ids]
            self.assertEqual(len(recording_ids),len(metadata['recordings']))
            self.assertEqual(len(set(recording_ids)),len(recording_ids))
            splits=metadata['splits']
            self.assertFalse(set(splits['train']) & set(splits['test']))
            self.assertFalse(set(splits['validation']) & set(splits['test']))
            self.assertFalse(set(splits['train']) & set(splits['validation']))
            raw=io.BytesIO()
            with wave.open(raw,'wb') as f:
                f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000); f.writeframes(b'\0'*32000)
            result=Detector(output).detect(raw.getvalue())
            self.assertTrue(0<=result['confidence']<=1)
            self.assertIn('FERRIS_MODEL_READY 1',(root/'weights.h').read_text())


if __name__=='__main__': unittest.main()
