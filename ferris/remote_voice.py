"""Voz executada em outra máquina, pelas rotas de áudio no formato da API OpenAI.

O LM Studio serve apenas texto, então Whisper e Qwen3-TTS ficam em um worker
separado (tools/voice_worker.py) na máquina com CPU sobrando. As classes aqui
têm a mesma interface das locais, para o servidor trocar uma pela outra.
"""
from __future__ import annotations

import json
import threading
from urllib import error, request

from .audio import read_wav
from .core import UserError

TIMEOUT_STT = 120
TIMEOUT_TTS = 200


def _call(url, body, content_type, token, timeout):
    req = request.Request(url, data=body, method='POST')
    req.add_header('Content-Type', content_type)
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return response.read(), response.headers.get('Content-Type', '')
    except error.HTTPError as exc:
        detail = ''
        try:
            detail = json.loads(exc.read()).get('error', {}).get('message', '')
        except (ValueError, OSError):
            pass
        raise UserError(detail or f'O serviço de voz remoto respondeu {exc.code}.') from exc
    except (error.URLError, OSError, TimeoutError) as exc:
        raise UserError('Não foi possível falar com o serviço de voz remoto. Confira se ele está no ar e a URL configurada.') from exc


class RemoteTranscriber:
    """Mesma interface de audio.Transcriber, transcrevendo em outra máquina."""

    def __init__(self, base_url, token=''):
        self.base = base_url.rstrip('/')
        self.token = token
        self.path = self.base  # o servidor usa isto como "há transcrição disponível"
        self.model = None
        self.lock = threading.Lock()

    def transcribe(self, raw):
        read_wav(raw)
        if not self.lock.acquire(blocking=False):
            raise UserError('A transcrição está ocupada. Tente novamente.')
        try:
            body, _ = _call(self.base + '/audio/transcriptions', raw, 'audio/wav',
                            self.token, TIMEOUT_STT)
            try:
                text = json.loads(body).get('text', '')
            except ValueError as exc:
                raise UserError('Resposta inválida do serviço de voz remoto.') from exc
            return text.strip()
        finally:
            self.lock.release()


class RemoteSpeech:
    """Mesma interface de speech.Speech, sintetizando em outra máquina com Qwen."""

    VOICES = ('ferris',)

    def __init__(self, base_url, token=''):
        self.base = base_url.rstrip('/')
        self.token = token
        self.voice = 'ferris'
        self.request_id = None

    def status(self):
        return {'ready': True, 'designed_ready': True, 'designed_preview_ready': True,
                'voice': self.voice, 'voices': list(self.VOICES), 'language': 'pt',
                'engine': f'Qwen3 VoiceDesign remoto ({self.base})'}

    def cancel(self, request_id=None):
        if request_id is not None and request_id != self.request_id:
            return
        try:
            _call(self.base + '/audio/speech/cancel', b'{}', 'application/json',
                  self.token, 10)
        except UserError:
            pass  # cancelar é melhor esforço; não vale derrubar o pedido do usuário

    def synthesize(self, text, voice='ferris', request_id=None):
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise UserError('Use de 1 a 2000 caracteres para a voz.')
        self.request_id = request_id
        try:
            body, kind = _call(self.base + '/audio/speech',
                               json.dumps({'input': text.strip()}).encode(),
                               'application/json', self.token, TIMEOUT_TTS)
            if 'audio' not in kind or not body:
                raise UserError('O serviço de voz remoto não devolveu áudio.')
            return body
        finally:
            self.request_id = None
