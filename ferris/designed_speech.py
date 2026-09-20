"""Descriptive voice generation on CPU. Slower than fixed-profile speech."""
import hashlib
import io
from pathlib import Path
import re
import threading
import time
import wave

from .core import UserError

PROMPT = ('A young adult male speaking native Brazilian Portuguese. A soft, light, sweet and endearing voice, '
          'warm and affectionate, with a gentle smile. Playful but calm, delicate conversational intonation, '
          'natural pauses, clear pronunciation. Intimate and friendly, like a cute helpful companion. '
          'Natural human timbre, not a narrator or announcer, not theatrical, not squeaky.')
PREVIEW = 'Oi! Que bom te ver por aqui. Como foi seu dia?'
FILES = ('config.json', 'tokenizer_config.json', 'vocab.json', 'merges.txt', 'cpu_int4/manifest.json',
         *(f'cpu_int4/{name}.onnx' for name in ('text_embed','codec_embed','talker_cache',
                                              'code_predictor','residual_embed','tok_decoder')))


class DesignedSpeech:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.pipe = None
        self.cancel_event = threading.Event()

    @property
    def ready(self):
        return all((self.folder/name).is_file() for name in FILES)

    def cache_path(self, text):
        key = hashlib.sha256(('qwen-design-int4-v1\n' + PROMPT + '\n' + text).encode()).hexdigest()
        return self.folder/'cache'/(key + '.wav')

    def cancel(self):
        self.cancel_event.set()

    def synthesize(self, text):
        path = self.cache_path(text)
        if path.is_file():
            return path.read_bytes()
        self.cancel_event.clear()
        deadline = time.monotonic() + 180
        def check():
            if self.cancel_event.is_set():
                raise UserError('Síntese cancelada.')
            if time.monotonic() > deadline:
                raise UserError('A voz expressiva excedeu 3 minutos neste PC. Use a voz rápida ou uma resposta mais curta.')
        if self.pipe is None:
            from .vendor.qwen_onnx import Pipeline
            self.pipe = Pipeline(str(self.folder/'cpu_int4'), str(self.folder))
        self.pipe.check_cancelled = check
        import numpy as np
        # Short chunks avoid unbounded autoregressive generation on this CPU.
        text = re.sub(r'\s+', ' ', text).strip()
        chunks = re.findall(r'.{1,140}(?:\s+|$)', text)
        if ''.join(chunks).strip() != text.strip():
            raise UserError('Use palavras e frases mais curtas para a voz expressiva.')
        audio = []
        for chunk in chunks:
            check()
            codes = self.pipe.generate(chunk.strip(), language='Portuguese', instruct=PROMPT,
                                       max_new_tokens=250, seed=42, verbose=False)
            if not 0 < len(codes) < 250:
                raise UserError('A voz não concluiu a frase. Tente uma frase mais curta.')
            check()
            audio.append(self.pipe.decode_chunked(codes[None]).reshape(-1))
            audio.append(np.zeros(4800, dtype=np.float32))
        check()
        samples = np.concatenate(audio[:-1])
        if not samples.size or not np.isfinite(samples).all():
            raise UserError('A síntese não produziu áudio válido.')
        result = io.BytesIO()
        with wave.open(result, 'wb') as wav:
            wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            wav.writeframes((np.clip(samples,-1,1)*32767).astype('<i2').tobytes())
        raw = result.getvalue()
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix('.part'); partial.write_bytes(raw); partial.replace(path)
        for old in sorted(path.parent.glob('*.wav'), key=lambda p:p.stat().st_mtime)[:-32]:
            old.unlink(missing_ok=True)
        return raw
