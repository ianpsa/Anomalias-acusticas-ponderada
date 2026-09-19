"""Shared C frontend + ONNX Runtime. Model inference never uses LM Studio."""
import ctypes
import json
import threading
from pathlib import Path
from .audio import read_wav
from .core import UserError

ROOT = Path(__file__).resolve().parent.parent


class Features:
    def __init__(self, library=None):
        path = Path(library or ROOT/'build'/'libferris_dsp.so')
        if not path.is_file():
            raise UserError('Compile a extração de áudio com python3 tools/build_dsp.py.')
        self.lib = ctypes.CDLL(str(path))
        self.lib.ferris_features.argtypes = [ctypes.POINTER(ctypes.c_int16), ctypes.POINTER(ctypes.c_float)]
        self.lib.ferris_features.restype = None
        self.lib.ferris_predict.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.c_float]
        self.lib.ferris_predict.restype = ctypes.c_float

    def __call__(self, pcm):
        if len(pcm) != 32000:
            raise UserError('O detector espera exatamente 1 segundo de áudio a 16 kHz.')
        import sys
        import array
        samples = array.array('h', pcm)
        if sys.byteorder != 'little':
            samples.byteswap()
        audio = (ctypes.c_int16 * 16000)(*samples)
        output = (ctypes.c_float * 150)()
        self.lib.ferris_features(audio, output)
        return list(output)


class Detector:
    def __init__(self, model_dir=None):
        self.folder = Path(model_dir or ROOT/'models')
        self.lock = threading.Lock()
        self.session = None

    @property
    def ready(self):
        return (self.folder/'wake.onnx').is_file() and (self.folder/'wake.json').is_file()

    def detect(self, raw):
        pcm = read_wav(raw, maximum=1)
        if len(pcm) != 32000:
            raise UserError('Envie exatamente um segundo para a detecção.')
        if not self.lock.acquire(blocking=False):
            raise UserError('Detector ocupado.')
        try:
            if self.session is None:
                if not self.ready:
                    raise UserError('Ainda não há modelo ONNX treinado. Grave os exemplos e execute tools/train_wake.py.')
                import onnxruntime as ort
                self.features = Features()
                self.metadata = json.loads((self.folder/'wake.json').read_text())
                options = ort.SessionOptions(); options.intra_op_num_threads = 1
                self.session = ort.InferenceSession(str(self.folder/'wake.onnx'), options, providers=['CPUExecutionProvider'])
            import numpy as np
            vector = np.asarray([self.features(pcm)], dtype=np.float32)
            confidence = float(self.session.run(None, {'features': vector})[0][0][0])
            return dict(confidence=confidence, detected=confidence >= self.metadata['threshold'])
        except ImportError as exc:
            raise UserError('Instale o extra train para executar o detector ONNX.') from exc
        finally:
            self.lock.release()
