"""Serviço de voz remoto: Whisper e Qwen3-TTS nas rotas de áudio da API OpenAI.

Roda na máquina que tem CPU/GPU sobrando e atende o Ferris pela rede local.
O LM Studio não implementa rotas de áudio, então este worker ocupa esse papel
com o mesmo formato de API, na porta ao lado.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import traceback
from email.parser import BytesParser
from email.policy import default
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from ferris.audio import Transcriber
from ferris.core import UserError
from ferris.speech import Speech
from ferris.designed_speech import PREVIEW

LIMIT = 16*1024*1024


def read_upload(body, content_type):
    """Aceita multipart no formato OpenAI ou um WAV cru no corpo."""
    if 'multipart/form-data' not in content_type:
        return body
    message = BytesParser(policy=default).parsebytes(
        ('Content-Type: ' + content_type + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + body)
    if message.is_multipart():
        for part in message.iter_parts():
            if part.get_param('name', header='content-disposition') == 'file':
                return part.get_payload(decode=True)
    raise UserError('Envie o áudio no campo "file" em multipart válido.')



class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    server_version = 'FerrisVoice/1.0'

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, fmt, *args):
        sys.stderr.write('%s %s\n' % (self.address_string(), fmt % args))

    def reply(self, code, payload, content_type='application/json'):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def fail(self, code, message):
        self.reply(code, {'error': {'message': message, 'type': 'invalid_request_error'}})

    def authorized(self):
        token = self.server.token
        if not token:
            return True
        sent = self.headers.get('Authorization', '')
        prefix = 'Bearer '
        return sent.startswith(prefix) and hmac.compare_digest(sent[len(prefix):], token)

    def do_OPTIONS(self):
        self.reply(204, b'', 'text/plain')

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self.authorized():
            return self.fail(401, 'Token inválido.')
        if path in ('/v1/models', '/api/v0/models'):
            return self.reply(200, {'object': 'list', 'data': [
                {'id': 'whisper-small', 'object': 'model', 'type': 'stt',
                 'state': 'loaded' if self.server.transcriber.model else 'not-loaded'},
                {'id': 'qwen3-tts-voicedesign', 'object': 'model', 'type': 'tts',
                 'state': 'loaded' if self.server.speech.designed.pipe else 'not-loaded'},
            ]})
        if path == '/health':
            return self.reply(200, {'ok': True, 'stt': bool(self.server.transcriber.path),
                                    'tts': self.server.speech.designed.ready,
                                    'preview_ready': self.server.speech.designed.cache_path(PREVIEW).is_file(),
                                    'scoped_cancel': True})
        self.fail(404, f'Unexpected endpoint or method. (GET {path})')

    def do_POST(self):
        path = urlsplit(self.path).path
        if not self.authorized():
            return self.fail(401, 'Token inválido.')
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            return self.fail(400, 'Content-Length inválido.')
        if self.headers.get('Transfer-Encoding') or not 0 < length <= LIMIT:
            return self.fail(413, 'Corpo ausente ou grande demais.')
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                raise UserError('Requisição incompleta.')
            if path == '/v1/audio/transcriptions':
                raw = read_upload(body, self.headers.get('Content-Type', ''))
                return self.reply(200, {'text': self.server.transcriber.transcribe(raw)})
            if path in ('/v1/audio/speech', '/v1/audio/speech/cancel'):
                if self.headers.get_content_type() != 'application/json':
                    raise UserError('Envie application/json.')
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise UserError('Envie um objeto JSON.')
                request_id = data.get('request_id')
                if request_id is not None and (not isinstance(request_id, str) or
                        not re.fullmatch(r'[a-zA-Z0-9-]{1,64}', request_id)):
                    raise UserError('Identificador de fala inválido.')
                if path.endswith('/cancel'):
                    if not request_id:
                        raise UserError('Informe request_id para cancelar somente a fala correspondente.')
                    self.server.speech.cancel(request_id)
                    return self.reply(200, {'cancelled': True})
                if data.get('voice', 'ferris') != 'ferris':
                    raise UserError('Este worker oferece apenas a voz Ferris expressiva.')
                if data.get('response_format', 'wav') != 'wav':
                    raise UserError('Este worker retorna áudio WAV.')
                return self.reply(200, self.server.speech.synthesize(
                    data.get('input'), 'ferris', request_id), 'audio/wav')
        except UserError as exc:
            return self.fail(400, str(exc))
        except (ValueError, UnicodeError):
            return self.fail(400, 'JSON inválido.')
        except (BrokenPipeError, ConnectionResetError):
            return
        except TimeoutError:
            return self.fail(408, 'Envio de áudio incompleto ou lento demais.')
        except Exception:
            traceback.print_exc()
            return self.fail(500, 'Falha no worker de voz. Confira o terminal do serviço.')
        self.fail(404, f'Unexpected endpoint or method. (POST {path})')


class Worker(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, data, token=''):
        super().__init__(address, Handler)
        whisper = os.environ.get('FERRIS_WHISPER_MODEL') or str(data/'whisper'/'small')
        self.transcriber = Transcriber(whisper if (Path(whisper)/'model.bin').is_file() else '')
        self.speech = Speech(data/'tts'/'supertonic-3')
        self.token = token


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8770)
    parser.add_argument('--data', type=Path, default=root/'data')
    args = parser.parse_args()

    token = os.environ.get('FERRIS_VOICE_TOKEN', '')
    worker = Worker((args.host, args.port), args.data, token)
    if not worker.transcriber.path:
        print('Aviso: Whisper ausente. Execute tools/models/setup_whisper.py.', file=sys.stderr)
    if not worker.speech.designed.ready:
        print('Aviso: voz Qwen ausente. Execute tools/models/setup_designed_tts.py.', file=sys.stderr)
    if not token and args.host != '127.0.0.1':
        print('Aviso: sem FERRIS_VOICE_TOKEN, qualquer máquina da rede pode usar este worker.', file=sys.stderr)
    if worker.speech.designed.ready:
        # Aquecer em segundo plano: a porta abre na hora e o primeiro pedido não
        # paga o carregamento das sessões ONNX nem a construção do tokenizer.
        threading.Thread(target=worker.speech.designed.warm, daemon=True).start()
    print(f'Worker de voz em http://{args.host}:{args.port}/v1', flush=True)
    try:
        worker.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        worker.server_close()


if __name__ == '__main__':
    main()
