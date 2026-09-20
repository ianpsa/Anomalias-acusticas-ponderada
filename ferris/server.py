from __future__ import annotations

import argparse
import base64
import binascii
import hmac
import json
import math
import os
import re
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .audio import Transcriber, read_recording
from .core import Assistant, Settings, UserError, greeting
from .detector import Detector
from .training import Training
from .device import Device, discover_port
from .remote_voice import RemoteSpeech, RemoteTranscriber
from .speech import Speech

STATIC = Path(__file__).parent / 'static'
MAX_BODY = 1_000_000


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, data, token='', device_token='', whisper='', model_dir=None, header=None, serial_port='', voice_url='', voice_token=''):
        self.data = Path(data)
        self.settings = Settings(self.data)
        self.assistant = Assistant(self.settings)
        local_whisper = self.data/'whisper'/'small'
        if voice_url:
            # Whisper e Qwen3-TTS rodam em outra máquina; veja tools/voice_worker.py.
            self.transcriber = RemoteTranscriber(voice_url, voice_token)
            self.speech = RemoteSpeech(voice_url, voice_token)
        else:
            self.transcriber = Transcriber(whisper or (str(local_whisper) if (local_whisper/'model.bin').is_file() else ''))
            self.speech = Speech(self.data/'tts/supertonic-3')
        self.detector = Detector(model_dir)
        self.device = Device(serial_port, self.assistant)
        export_header = header or (Path(model_dir)/'model_weights.h' if model_dir else None)
        self.training = Training(self.data, self.detector, export_header, self.device)
        self.token = token
        self.device_token = device_token
        super().__init__(address, Handler)
        self.device.start()

    def server_close(self):
        self.device.stop()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def log_message(self, fmt, *args):
        # Never log request payloads, keys or transcriptions.
        if len(args) > 1:
            print(f'HTTP {args[1]}', flush=True)

    def reply(self, data, status=200, mime='application/json; charset=utf-8'):
        raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode() if isinstance(data, (dict, list)) else data
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self, device=False):
        host = self.headers.get('Host', '')
        try:
            hostname = urlsplit('http://' + host).hostname
        except ValueError:
            hostname = None
        if not self.server.token and hostname not in ('localhost', '127.0.0.1', '::1'):
            self.reply({'error': 'Acesso local apenas. Para rede, configure FERRIS_TOKEN.'}, 403)
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in ('http://' + host, 'https://' + host):
            self.reply({'error': 'Origem não permitida.'}, 403)
            return False
        expected = self.server.device_token if device else self.server.token
        if device and not expected:
            self.reply({'error': 'Configure FERRIS_DEVICE_TOKEN para conectar o ESP32.'}, 403)
            return False
        if expected and not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + expected):
            self.reply({'error': 'Informe o token de acesso ao Ferris.'}, 401)
            return False
        return True

    def body(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise UserError('Tamanho inválido.')
        if size < 2 or size > MAX_BODY or self.headers.get('Transfer-Encoding'):
            raise UserError('Corpo ausente ou muito grande (máximo 1 MB).')
        if self.headers.get_content_type() != 'application/json':
            raise UserError('Envie application/json.')
        raw = self.rfile.read(size)
        if len(raw) != size:
            raise UserError('Requisição incompleta.')
        try:
            data = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError):
            raise UserError('JSON inválido.')
        if not isinstance(data, dict):
            raise UserError('Envie um objeto JSON.')
        return data

    def do_GET(self):
        path = urlsplit(self.path).path
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/capture.js': ('capture.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if path in assets:
            filename, mime = assets[path]
            return self.reply((STATIC / filename).read_bytes(), mime=mime)
        if not self.authorized():
            return
        try:
            if path == '/api/status':
                counts = {label: len(list((self.server.data/'recordings'/label).glob('*/*.wav'))) for label in ('ferris', 'other', 'noise')}
                self.reply(dict(settings=self.server.settings.public(), greeting=greeting(self.server.settings.get()),
                                recordings=counts, local_voice=bool(self.server.transcriber.path),
                                recording_sessions=len({p.parent.name for p in (self.server.data/'recordings').glob('*/*/*.wav')}),
                                training=self.server.training.status(), device=self.server.device.status(),
                                hardware=self.server.assistant.hardware_state(), speech=self.server.speech.status(), wake_model=self.server.detector.ready))
            elif path == '/api/training':
                self.reply(self.server.training.status())
            elif path == '/api/models':
                self.reply({'models': self.server.assistant.models()})
            elif path == '/api/events':
                since = int(parse_qs(urlsplit(self.path).query).get('since', ['0'])[0])
                with self.server.assistant.lock:
                    self.reply({'events': [e for e in self.server.assistant.events if e['id'] > since],
                                'latest': self.server.assistant.event_id,
                                'hardware': self.server.assistant.hardware_state()})
            else:
                self.reply({'error': 'Rota não encontrada.'}, 404)
        except (UserError, ValueError) as exc:
            self.reply({'error': str(exc)}, 400)

    def do_POST(self):
        path = urlsplit(self.path).path
        if not self.authorized(device=path == '/api/device/wake'):
            return
        try:
            data = self.body()
            if path == '/api/speech/cancel':
                request_id = data.get('request_id')
                if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,64}', request_id):
                    raise UserError('Identificador de fala inválido.')
                self.server.speech.cancel(request_id)
                self.reply({'ok': True})
            elif path == '/api/device/record/cancel':
                self.server.device.cancel_recording()
                self.reply({'ok': True})
            elif path == '/api/device/record':
                self.reply(self.server.device.record(), mime='audio/wav')
            elif path == '/api/speech':
                hardware = self.server.assistant.hardware_state()
                if hardware['muted']:
                    raise UserError('O botão do ESP32 está em mute.')
                raw = self.server.speech.synthesize(data.get('text'), data.get('voice', 'ferris'), data.get('request_id'))
                after = self.server.assistant.hardware_state()
                if after['muted'] or after['revision'] != hardware['revision']:
                    raise UserError('Voz descartada: o botão de mute foi acionado.')
                self.reply(raw, mime='audio/wav')
            elif path == '/api/train':
                self.reply(self.server.training.start(data.get('split_mode'), data.get('flash', False)), 202)
            elif path == '/api/settings':
                self.reply(self.server.settings.update(data))
            elif path == '/api/chat':
                text = data.get('text', '')
                session = self.session(data)
                if not isinstance(text, str) or not text.strip() or len(text) > 2000:
                    raise UserError('Escreva uma mensagem de 1 a 2000 caracteres.')
                self.reply(self.server.assistant.chat(text.strip(), session, data.get('search') is True))
            elif path == '/api/reset':
                self.server.assistant.reset(self.session(data))
                self.reply({'ok': True})
            elif path in ('/api/wake', '/api/device/wake'):
                if path == '/api/device/wake' and data.get('type') == 'state':
                    state = self.server.assistant.update_hardware(data)
                    if state['muted']: self.server.device.cancel_recording()
                    self.reply(state)
                    return
                device = data.get('device', 'desktop')
                confidence = data.get('confidence')
                metrics = data.get('metrics', {})
                if not isinstance(device, str) or len(device) > 60:
                    raise UserError('Dispositivo inválido.')
                if confidence is not None and (type(confidence) not in (int, float) or not 0 <= confidence <= 1):
                    raise UserError('Confiança inválida.')
                if not isinstance(metrics, dict) or len(metrics) > 12 or any(type(v) not in (float, int) or not math.isfinite(v) or v < 0 for v in metrics.values()):
                    raise UserError('Métricas inválidas.')
                event_id = data.get('event_id') if path == '/api/device/wake' else None
                if event_id is not None and (not isinstance(event_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',event_id)):
                    raise UserError('Identificador de evento inválido.')
                self.reply(self.server.assistant.wake(device, confidence, metrics, event_id))
            elif path in ('/api/recordings', '/api/recordings/esp', '/api/transcribe', '/api/detect'):
                hardware = self.server.assistant.hardware_state()
                if hardware['muted']:
                    raise UserError('O botão do ESP32 está em mute. Desmute a placa antes de usar o microfone.')
                label, group = data.get('label'), data.get('group')
                if path.startswith('/api/recordings') and (label not in ('ferris', 'other', 'noise') or not isinstance(group, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}', group)):
                    raise UserError('Informe classe e sessão de gravação válidas.')
                try:
                    raw = self.server.device.record() if path == '/api/recordings/esp' else base64.b64decode(data.get('audio', ''), validate=True)
                except (ValueError, TypeError, binascii.Error):
                    raise UserError('Áudio inválido.')
                if path == '/api/detect':
                    self.reply(self.server.detector.detect(raw))
                    return
                if path == '/api/transcribe':
                    text = self.server.transcriber.transcribe(raw)
                    after = self.server.assistant.hardware_state()
                    if after['muted'] or after['revision'] != hardware['revision']:
                        raise UserError('Transcrição descartada: o botão de mute foi acionado.')
                    self.reply({'text': text})
                    return
                read_recording(raw)
                after = self.server.assistant.hardware_state()
                if after['muted'] or after['revision'] != hardware['revision']:
                    raise UserError('Gravação descartada: o botão de mute foi acionado.')
                label, group = data.get('label'), data.get('group')
                if label not in ('ferris', 'other', 'noise') or not isinstance(group, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}', group):
                    raise UserError('Informe classe e sessão de gravação válidas.')
                folder = self.server.data / 'recordings' / label / group
                folder.mkdir(parents=True, exist_ok=True)
                name = ('esp32-' if path == '/api/recordings/esp' else 'pc-') + f'{time.time_ns()}-{secrets.token_hex(4)}.wav'
                partial = folder / (name+'.part')
                with open(partial, 'xb') as f:
                    f.write(raw)
                partial.replace(folder / name)
                self.reply({'saved': True, 'label': label, 'file': name}, 201)
            else:
                self.reply({'error': 'Rota não encontrada.'}, 404)
        except UserError as exc:
            self.reply({'error': str(exc)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            # Keep unexpected exceptions visible locally without leaking secrets to clients.
            import traceback
            traceback.print_exc()
            self.reply({'error': 'Erro interno. Verifique o terminal do Ferris.'}, 500)

    @staticmethod
    def session(data):
        session = data.get('session', '')
        if not isinstance(session, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', session):
            raise UserError('Sessão inválida.')
        return session


def main():
    parser = argparse.ArgumentParser(description='Ferris: painel e ponte para LM Studio')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data', type=Path, default=Path('data'))
    parser.add_argument('--models', type=Path, default=None, help='Diretório dos modelos ONNX')
    parser.add_argument('--serial-port', default=os.getenv('FERRIS_SERIAL_PORT', discover_port()),
                        help='Porta USB do ESP32; vazio desabilita USB e mantém Wi-Fi')
    parser.add_argument('--voice-url', default=os.getenv('FERRIS_VOICE_URL', ''),
                        help='Worker de voz remoto, por exemplo http://192.168.15.17:8770/v1')
    args = parser.parse_args()
    token = os.getenv('FERRIS_TOKEN', '')
    if args.host not in ('127.0.0.1', 'localhost') and len(token) < 24:
        parser.error('Para servir na rede, configure FERRIS_TOKEN com pelo menos 24 caracteres.')
    with Server((args.host, args.port), args.data, token, os.getenv('FERRIS_DEVICE_TOKEN', ''),
                os.getenv('FERRIS_WHISPER_MODEL', ''), model_dir=args.models, serial_port=args.serial_port,
                voice_url=args.voice_url, voice_token=os.getenv('FERRIS_VOICE_TOKEN', '')) as server:
        print(f'Ferris em http://{args.host}:{server.server_port} — Ctrl+C para encerrar', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
