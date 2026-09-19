"""Local Portuguese speech with unmodified Supertonic ONNX voice profiles."""
from collections import OrderedDict
import io
from pathlib import Path
import re
import threading
import wave

from .core import UserError

VOICES = tuple(f'M{i}' for i in range(1, 6))
MODEL_FILES = ('onnx/duration_predictor.onnx', 'onnx/text_encoder.onnx',
               'onnx/vector_estimator.onnx', 'onnx/vocoder.onnx',
               'onnx/tts.json', 'onnx/unicode_indexer.json')


class Speech:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.voice = 'M1'
        self.engine = None
        self.lock = threading.Lock()
        self.cache = OrderedDict()

    @property
    def ready(self):
        return all((self.folder/name).is_file() for name in (
            *MODEL_FILES, *(f'voice_styles/{voice}.json' for voice in VOICES)))

    def status(self):
        return {'ready': self.ready, 'voice': self.voice, 'voices': list(VOICES),
                'language': 'pt', 'engine': 'Supertonic 3 ONNX'}

    def _load(self):
        from supertonic import TTS
        return TTS(model='supertonic-3', model_dir=self.folder, auto_download=False,
                   intra_op_num_threads=4, inter_op_num_threads=1)

    def synthesize(self, text, voice='M1'):
        if voice not in VOICES:
            raise UserError('Escolha uma das vozes masculinas M1 a M5.')
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise UserError('Use de 1 a 2000 caracteres para a voz.')
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        text = re.sub(r'https?://\S+', '', text)
        text = re.sub(r'[*#`]+', '', text).strip()
        if not text:
            raise UserError('Não há texto para falar.')
        if not self.ready:
            raise UserError('Instale o extra tts e execute tools/setup_tts.py no PC de destino para habilitar as vozes.')
        if not self.lock.acquire(blocking=False):
            raise UserError('A voz está sendo preparada. Aguarde um instante e tente novamente.')
        try:
            key = (text, voice)
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            if self.engine is None:
                self.engine = self._load()
            import numpy as np
            samples, _ = self.engine.synthesize(
                text, voice_style=self.engine.get_voice_style(voice), lang='pt',
                total_steps=16, speed=1.0, max_chunk_length=240, silence_duration=.25)
            samples = np.asarray(samples).reshape(-1)
            if not samples.size or not np.isfinite(samples).all():
                raise UserError('A síntese não produziu áudio válido.')
            # Preserve the native sample rate and pitch; no chipmunk/slowdown effects.
            pcm = (np.clip(samples, -1, 1)*32767).astype('<i2').tobytes()
            result = io.BytesIO()
            with wave.open(result, 'wb') as wav:
                wav.setnchannels(1); wav.setsampwidth(2)
                wav.setframerate(self.engine.sample_rate); wav.writeframes(pcm)
            raw = result.getvalue()
            self.cache[key] = raw
            while len(self.cache) > 8:
                self.cache.popitem(last=False)
            return raw
        except ImportError as exc:
            raise UserError('Instale o extra tts no PC de destino para gerar a voz.') from exc
        except (OSError, RuntimeError, ValueError) as exc:
            raise UserError('Falha na voz local. Confira os arquivos Supertonic e as dependências de síntese.') from exc
        finally:
            self.lock.release()
