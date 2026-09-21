"""Clients for the Ferris voice worker; LM Studio receives text separately."""
from __future__ import annotations

import io
import http.client
import json
import threading
import time
import uuid
import wave
from urllib import error, request

from .audio import read_wav
from .core import NoRedirect, UserError, checked_url

TIMEOUT_STT = 120
TIMEOUT_TTS = 200
MAX_RESPONSE = 24 * 1024 * 1024


class _ConnectTimeout:
    def connect(self):
        read_timeout = self.timeout
        self.timeout = min(read_timeout, 5)
        try:
            super().connect()
            self.sock.settimeout(read_timeout)
        finally:
            self.timeout = read_timeout


class _HTTPConnection(_ConnectTimeout, http.client.HTTPConnection):
    pass


class _HTTPSConnection(_ConnectTimeout, http.client.HTTPSConnection):
    pass


class _HTTPHandler(request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_HTTPConnection, req)


class _HTTPSHandler(request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_HTTPSConnection, req, context=self._context)


def _call(url, body, content_type, token, timeout):
    req = request.Request(url, data=body, method='GET' if body is None else 'POST')
    req.add_header('Content-Type', content_type)
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with request.build_opener(NoRedirect, _HTTPHandler, _HTTPSHandler).open(req, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise UserError('Resposta do serviço de voz remoto excedeu o limite.')
            return raw, response.headers.get('Content-Type', '')
    except error.HTTPError as exc:
        detail = ''
        try:
            payload = json.loads(exc.read(8192))
            detail = payload.get('error', '') if isinstance(payload, dict) else ''
            if isinstance(detail, dict):
                detail = detail.get('message', '')
        except (ValueError, OSError):
            pass
        raise UserError(detail if isinstance(detail, str) and detail else
                        f'O serviço de voz remoto respondeu {exc.code}. Confira URL e token.') from exc
    except (error.URLError, OSError, TimeoutError, http.client.HTTPException) as exc:
        raise UserError('Não foi possível falar com o serviço de voz remoto. Confira se ele está no ar e a URL configurada.') from exc


class RemoteTranscriber:
    def __init__(self, base_url, token=''):
        self.base = checked_url(base_url)
        self.token = token
        self.path = self.base
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
                payload = json.loads(body)
                if not isinstance(payload, dict) or not isinstance(payload.get('text'), str):
                    raise ValueError()
                return payload['text'].strip()
            except ValueError as exc:
                raise UserError('Resposta inválida do serviço de voz remoto.') from exc
        finally:
            self.lock.release()


class RemoteSpeech:
    VOICES = ('ferris',)

    def __init__(self, base_url, token=''):
        self.base = checked_url(base_url)
        self.token = token
        self.voice = 'ferris'
        self.request_id = None
        self.lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.health_lock = threading.Lock()
        self.health_time = 0
        self.health = {}
        self.cancelled = False

    def status(self):
        # Cache only briefly so polling does not continuously contact the worker.
        with self.health_lock:
            if time.monotonic() - self.health_time > 3:
                try:
                    body, _ = _call(self.base.removesuffix('/v1') + '/health', None,
                                    'application/json', self.token, 3)
                    payload = json.loads(body)
                    if not isinstance(payload, dict) or not all(type(payload.get(k)) is bool for k in ('stt', 'tts')):
                        raise UserError('Health check inválido. Configure a porta do worker de voz, não a do LM Studio.')
                    self.health = {'stt': payload['stt'], 'tts': payload['tts'],
                                   'preview': payload.get('preview_ready') is True, 'error': ''}
                except (UserError, ValueError) as exc:
                    self.health = {'stt': False, 'tts': False, 'preview': False, 'error': str(exc)}
                self.health_time = time.monotonic()
            return {'ready': self.health.get('tts', False), 'remote': True, 'url': self.base,
                    'transcription_ready': self.health.get('stt', False),
                    'error': self.health.get('error', ''),
                    'designed_ready': self.health.get('tts', False),
                    'designed_preview_ready': self.health.get('preview', False),
                    'voice': self.voice, 'voices': list(self.VOICES), 'language': 'pt',
                    'engine': 'Qwen3 VoiceDesign remoto'}

    def cancel(self, request_id=None):
        # Hold this lock until cancellation is sent. An old cancel must not arrive
        # after this client starts another synthesis, including with older workers.
        with self.state_lock:
            active = self.request_id
            if active is None or (request_id is not None and request_id != active):
                return
            self.cancelled = True
            try:
                _call(self.base + '/audio/speech/cancel',
                      json.dumps({'request_id': active}).encode(), 'application/json', self.token, 3)
            except UserError:
                pass  # The cancelled result is discarded locally even if offline.

    def synthesize(self, text, voice='ferris', request_id=None):
        if voice not in self.VOICES:
            raise UserError('O worker remoto oferece a voz Ferris expressiva.')
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise UserError('Use de 1 a 2000 caracteres para a voz.')
        if not self.lock.acquire(blocking=False):
            raise UserError('A voz está sendo preparada. Aguarde um instante e tente novamente.')
        try:
            with self.state_lock:
                self.request_id = request_id or str(uuid.uuid4())
                self.cancelled = False
            body, kind = _call(self.base + '/audio/speech',
                               json.dumps({'input': text.strip(), 'voice': voice,
                                           'request_id': self.request_id, 'response_format': 'wav'}).encode(),
                               'application/json', self.token, TIMEOUT_TTS)
            with self.state_lock:
                if self.cancelled:
                    raise UserError('Síntese cancelada.')
            if 'audio' not in kind or not body:
                raise UserError('O serviço de voz remoto não devolveu áudio.')
            try:
                with wave.open(io.BytesIO(body)) as wav:
                    frames = wav.getnframes()
                    if (wav.getnchannels() != 1 or wav.getsampwidth() != 2 or frames <= 0
                            or len(wav.readframes(frames)) != frames * 2):
                        raise ValueError()
            except (wave.Error, EOFError, ValueError) as exc:
                raise UserError('O serviço de voz remoto devolveu WAV inválido.') from exc
            return body
        finally:
            with self.state_lock:
                self.request_id = None
            self.lock.release()
