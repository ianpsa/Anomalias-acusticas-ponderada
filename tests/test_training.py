import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import wave

from ferris.core import Assistant, Settings, UserError
from ferris.detector import Detector
from ferris.device import parse_event
from ferris.training import Training


class DeviceTests(unittest.TestCase):
    def test_usb_and_wifi_deduplicate_one_wake(self):
        payload={'device':'esp32-ferris','event_id':'boot-1','confidence':.95,'metrics':{'decision_us':100}}
        event=parse_event(b'FERRIS_WAKE '+json.dumps(payload).encode()+b'\n')
        self.assertEqual(event,payload)
        for line in [b'normal log',b'FERRIS_WAKE {}',b'FERRIS_WAKE []',
                     b'FERRIS_WAKE '+json.dumps({**payload,'confidence':float('nan')}).encode()]:
            self.assertIsNone(parse_event(line))
        with tempfile.TemporaryDirectory() as tmp:
            assistant=Assistant(Settings(Path(tmp)))
            usb=assistant.wake(event['device'],event['confidence'],event['metrics'],event['event_id'])
            wifi=assistant.wake(event['device'],event['confidence'],event['metrics'],event['event_id'])
            self.assertEqual(usb,wifi)
            self.assertEqual(len(assistant.events),1)
            assistant.wake(event['device'],.9,{},'boot-2')
            self.assertEqual(len(assistant.events),2)


@unittest.skipUnless(importlib.util.find_spec('onnxruntime') and importlib.util.find_spec('sklearn'),'Install .[train]')
class TrainingTests(unittest.TestCase):
    def test_background_training_activation_restart_and_failure_preserve_model(self):
        import numpy as np
        rng=np.random.default_rng(7)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); data=root/'data'; model=root/'models'; header=root/'firmware/model_weights.h'
            for label,hz in [('ferris',900),('other',2400),('noise',3000)]:
                folder=data/'recordings'/label/'session1'; folder.mkdir(parents=True)
                for i in range(12):
                    t=np.arange(32000)/16000
                    pcm=(8000*np.sin(2*np.pi*(hz+i)*t)+rng.normal(0,100,32000)).astype('<i2').tobytes()
                    with wave.open(str(folder/f'{i}.wav'),'wb') as wav:
                        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000); wav.writeframes(pcm)
            detector=Detector(model); job=Training(data,detector,header)
            with self.assertRaises(UserError): job.start('sessions')
            with self.assertRaises(UserError): job.start('recordings',True)
            self.assertEqual(job.start('recordings')['state'],'running')
            with self.assertRaisesRegex(UserError,'andamento'): job.start('recordings')
            job.worker.join(45)
            self.assertFalse(job.worker.is_alive())
            self.assertEqual(job.status()['state'],'ready',job.status())
            before=detector.summary(); pointer=(model/'active.json').read_bytes(); old_header=header.read_bytes()
            restarted=Detector(model)
            self.assertEqual(restarted.summary(),before)
            buf=io.BytesIO()
            with wave.open(buf,'wb') as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000); wav.writeframes(b'\0'*32000)
            self.assertIn('confidence',restarted.detect(buf.getvalue()))
            broken=model/'runs/broken'; shutil.copytree(detector.folder,broken)
            (broken/'wake.onnx').write_bytes(b'corrupt')
            with self.assertRaises(UserError): detector.activate(broken,header)
            self.assertEqual((model/'active.json').read_bytes(),pointer)
            # A real failing subprocess must leave the last usable bundle intact.
            source=data/'recordings/ferris/session1/0.wav'
            shutil.copyfile(source,source.with_name('duplicate.wav'))
            job.start('recordings'); job.worker.join(45)
            self.assertFalse(job.worker.is_alive())
            self.assertEqual(job.status()['state'],'error')
            self.assertEqual(detector.summary(),before)
            self.assertEqual(header.read_bytes(),old_header)
            self.assertEqual((model/'active.json').read_bytes(),pointer)
