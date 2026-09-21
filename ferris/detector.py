"""Shared C frontend + ONNX Runtime. Model inference never uses LM Studio."""
import ctypes
import hashlib
import json
import math
import os
import tempfile
import threading
from pathlib import Path
from .audio import read_wav
from .core import UserError

ROOT = Path(__file__).resolve().parent.parent


class Features:
    def __init__(self, library=None):
        path = Path(library or ROOT/'build'/'libferris_dsp.so')
        if not path.is_file():
            raise UserError('Compile a extração de áudio com python3 tools/training/build_dsp.py.')
        self.lib = ctypes.CDLL(str(path))
        self.lib.ferris_features.argtypes = [ctypes.POINTER(ctypes.c_int16), ctypes.POINTER(ctypes.c_float)]
        self.lib.ferris_features.restype = None
        self.lib.ferris_predict.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.c_float]
        self.lib.ferris_predict.restype = ctypes.c_float
        self.lib.ferris_predict_hidden.argtypes = [ctypes.POINTER(ctypes.c_float)] * 4 + [ctypes.c_float]
        self.lib.ferris_predict_hidden.restype = ctypes.c_float

    def __call__(self, pcm):
        if len(pcm) != 32000:
            raise UserError('O detector espera exatamente 1 segundo de áudio a 16 kHz.')
        import sys
        if sys.byteorder != 'little':
            import array
            samples = array.array('h', pcm)
            samples.byteswap()
            pcm = samples.tobytes()
        audio = (ctypes.c_int16 * 16000).from_buffer_copy(pcm)
        output = (ctypes.c_float * 150)()
        self.lib.ferris_features(audio, output)
        return list(output)


class Detector:
    def __init__(self, model_dir=None):
        self.root = Path(model_dir or ROOT/'models').resolve()
        self.folder = self.root
        pointer = self.root/'active.json'
        if pointer.is_file():
            folder = (self.root/json.loads(pointer.read_text())['directory']).resolve()
            folder.relative_to(self.root)
            self.folder = folder
        self.lock = threading.RLock()
        self.session = None

    @property
    def ready(self):
        with self.lock:
            return (self.folder/'wake.onnx').is_file() and (self.folder/'wake.json').is_file()

    def summary(self):
        with self.lock:
            if not self.ready:
                return None
            metadata = json.loads((self.folder/'wake.json').read_text())
            return {k: metadata.get(k) for k in ('model_sha256', 'threshold', 'split_mode',
                                                'validation', 'test', 'personal_validation', 'personal_test', 'metric_unit', 'limitations')}

    @staticmethod
    def _load(folder):
        import numpy as np
        import onnxruntime as ort
        metadata = json.loads((folder/'wake.json').read_text())
        dsp_hash = hashlib.sha256((ROOT/'firmware/components/ferris_dsp/ferris_dsp.c').read_bytes()).hexdigest()
        if metadata.get('dsp_sha256') != dsp_hash:
            raise UserError('O modelo usa outra versão do extrator de áudio. Treine novamente e atualize o firmware do ESP32.')
        if (metadata.get('features') != 150 or metadata.get('sample_rate') != 16000
                or metadata.get('samples') != 16000
                or not math.isfinite(metadata['threshold']) or not 0 <= metadata['threshold'] <= 1
                or hashlib.sha256((folder/'wake.onnx').read_bytes()).hexdigest() != metadata.get('model_sha256')):
            raise UserError('Modelo inválido ou incompatível com o detector.')
        options = ort.SessionOptions(); options.intra_op_num_threads = 1
        session = ort.InferenceSession(str(folder/'wake.onnx'), options, providers=['CPUExecutionProvider'])
        result = session.run(None, {'features': np.zeros((1,150), dtype=np.float32)})[0]
        if result.shape != (1,1) or not np.isfinite(result).all() or not ((result >= 0) & (result <= 1)).all():
            raise UserError('Saída do modelo inválida.')
        return Features(), metadata, session

    @staticmethod
    def _replace(path, raw):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.'+path.name)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
            os.replace(tmp, path)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def activate(self, folder, header_path):
        folder = Path(folder).resolve()
        relative = folder.relative_to(self.root)
        features, metadata, session = self._load(folder)
        header = (folder/'model_weights.h').read_bytes()
        # Publish an immutable bundle only after the model can run. Readers hold
        # the same lock, so in-flight inference always finishes on one version.
        with self.lock:
            previous_header = header_path.read_bytes() if header_path.exists() else None
            self._replace(header_path, header)
            try:
                self._replace(self.root/'active.json', json.dumps({'directory': str(relative)}).encode())
            except Exception:
                if previous_header is None:
                    header_path.unlink(missing_ok=True)
                else:
                    self._replace(header_path, previous_header)
                raise
            self.folder = folder
            self.features, self.metadata, self.session = features, metadata, session

    def detect(self, raw):
        pcm = read_wav(raw, maximum=1)
        if len(pcm) != 32000:
            raise UserError('Envie exatamente um segundo para a detecção.')
        if not self.lock.acquire(blocking=False):
            raise UserError('Detector ocupado.')
        try:
            if self.session is None:
                if not self.ready:
                    raise UserError('Ainda não há modelo ONNX treinado. Grave os exemplos e execute tools/training/train_wake.py.')
                self.features, self.metadata, self.session = self._load(self.folder)
            import numpy as np
            vector = np.asarray([self.features(pcm)], dtype=np.float32)
            confidence = float(self.session.run(None, {'features': vector})[0][0][0])
            return dict(confidence=confidence, detected=confidence >= self.metadata['threshold'])
        except ImportError as exc:
            raise UserError('Instale o extra train para executar o detector ONNX.') from exc
        finally:
            self.lock.release()
