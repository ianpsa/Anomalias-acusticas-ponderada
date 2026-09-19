"""Local Brazilian Portuguese neural speech; no online inference or voice cloning."""
from collections import OrderedDict
import io
from pathlib import Path
import re
import threading
import wave

from .core import UserError


class Speech:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.voice = 'pm_alex'
        self.engine = None
        self.lock = threading.Lock()
        self.cache = OrderedDict()

    @property
    def ready(self):
        return all((self.folder/name).is_file() for name in ('kokoro-v1.0.onnx','voices-v1.0.bin'))

    def status(self):
        return {'ready': self.ready, 'voice': 'Alex', 'language': 'pt-BR', 'engine': 'Kokoro ONNX'}

    def synthesize(self, text, style='soft'):
        if style not in ('soft','natural'):
            raise UserError('Escolha o estilo suave ou natural.')
        if not isinstance(text,str) or not text.strip() or len(text)>2000:
            raise UserError('Use de 1 a 2000 caracteres para a voz.')
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)',r'\1',text)
        text = re.sub(r'https?://\S+','',text)
        text = re.sub(r'[*#`]+','',text).strip()
        if not text:
            raise UserError('Não há texto para falar.')
        if not self.ready:
            raise UserError('Instale o extra tts e execute tools/setup_tts.py no PC de destino para habilitar a voz brasileira.')
        if not self.lock.acquire(blocking=False):
            raise UserError('A voz está sendo preparada. Aguarde um instante e tente novamente.')
        try:
            key = (text,style)
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            if self.engine is None:
                import onnxruntime as ort
                import numpy as np
                from kokoro_onnx import Kokoro
                from kokoro_onnx.config import MAX_PHONEME_LENGTH, SAMPLE_RATE
                class FloatSpeedVoice(Kokoro):
                    def _split_phonemes(self, phonemes):
                        # The upstream splitter can leave a long sentence above
                        # the model limit. Preserve its remainder in more batches.
                        batches = []
                        for part in super()._split_phonemes(phonemes):
                            while len(part) > MAX_PHONEME_LENGTH:
                                cut = part.rfind(' ', 0, MAX_PHONEME_LENGTH + 1)
                                if cut <= 0: cut = MAX_PHONEME_LENGTH
                                batches.append(part[:cut]); part = part[cut:].lstrip()
                            if part: batches.append(part)
                        return batches

                    def _create_audio(self, phonemes, voice, speed):
                        # kokoro-onnx 0.4.9 casts speed to int32 for input_ids,
                        # but this verified export requires float32, including .96.
                        tokens = self.tokenizer.tokenize(phonemes[:MAX_PHONEME_LENGTH])
                        inputs = {'input_ids': np.asarray([[0,*tokens,0]],dtype=np.int64),
                                  'style': np.asarray(voice[len(tokens)],dtype=np.float32).reshape(1,256),
                                  'speed': np.asarray([speed],dtype=np.float32)}
                        return self.sess.run(None,inputs)[0].reshape(-1), SAMPLE_RATE
                options = ort.SessionOptions(); options.intra_op_num_threads = 4
                options.inter_op_num_threads = 1
                session = ort.InferenceSession(str(self.folder/'kokoro-v1.0.onnx'), options,
                                               providers=['CPUExecutionProvider'])
                self.engine = FloatSpeedVoice.from_session(session, str(self.folder/'voices-v1.0.bin'))
            import numpy as np
            samples, rate = self.engine.create(text, voice=self.voice, speed=.96 if style == 'soft' else 1.0, lang='pt-br')
            # A subtle one-semitone lift, compensated by the synthesis pace.
            # Keep the original masculine voice available for direct comparison.
            if style == 'soft':
                rate = round(rate * 2**(1/12))
            if not len(samples) or not np.isfinite(samples).all():
                raise UserError('A síntese não produziu áudio válido.')
            pcm = (np.clip(samples,-1,1)*32767).astype('<i2').tobytes()
            result = io.BytesIO()
            with wave.open(result,'wb') as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate); wav.writeframes(pcm)
            raw = result.getvalue()
            self.cache[key] = raw
            while len(self.cache)>8:
                self.cache.popitem(last=False)
            return raw
        except ImportError as exc:
            raise UserError('Instale o extra tts no PC de destino para gerar a voz.') from exc
        except (OSError, RuntimeError, ValueError) as exc:
            raise UserError('Falha na voz local. Confira os arquivos Kokoro e as dependências de síntese.') from exc
        finally:
            self.lock.release()
