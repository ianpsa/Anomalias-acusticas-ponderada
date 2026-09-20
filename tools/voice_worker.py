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
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ferris.audio import Transcriber
from ferris.core import UserError
from ferris.designed_speech import DesignedSpeech

LIMIT = 16*1024*1024


def read_upload(body, content_type):
    """Aceita multipart no formato OpenAI ou um WAV cru no corpo."""
    if 'multipart/form-data' not in content_type:
        return body
    _, _, boundary = content_type.partition('boundary=')
    boundary = boundary.split(';')[0].strip().strip('"')
    if not boundary:
        raise UserError('Multipart sem boundary.')
    for part in body.split(b'--' + boundary.encode()):
        head, sep, data = part.partition(b'\r\n\r\n')
        if sep and b'name="file"' in head:
            return data.rsplit(b'\r\n', 1)[0]
    raise UserError('Envie o áudio no campo "file".')


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'FerrisVoice/1.0'

    def log_message(self, fmt, *args):
        sys.stderr.write('%s %s\n' % (self.address_string(), fmt % args))

    def reply(self, code, payload, content_type='application/json'):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
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
        if path in ('/v1/models', '/api/v0/models'):
            return self.reply(200, {'object': 'list', 'data': [
                {'id': 'whisper-small', 'object': 'model', 'type': 'stt',
                 'state': 'loaded' if self.server.transcriber.model else 'not-loaded'},
                {'id': 'qwen3-tts-voicedesign', 'object': 'model', 'type': 'tts',
                 'state': 'loaded' if self.server.speech.pipe else 'not-loaded'},
            ]})
        if path == '/health':
            return self.reply(200, {'ok': True, 'stt': bool(self.server.transcriber.path),
                                    'tts': self.server.speech.ready})
        self.fail(404, f'Unexpected endpoint or method. (GET {path})')

    def do_POST(self):
        path = urlsplit(self.path).path
        if not self.authorized():
            return self.fail(401, 'Token inválido.')
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            return self.fail(400, 'Content-Length inválido.')
        if not 0 < length <= LIMIT:
            return self.fail(413, 'Corpo ausente ou grande demais.')
        body = self.rfile.read(length)
        try:
            if path == '/v1/audio/transcriptions':
                raw = read_upload(body, self.headers.get('Content-Type', ''))
                return self.reply(200, {'text': self.server.transcriber.transcribe(raw)})
            if path == '/v1/audio/speech':
                data = json.loads(body)
                text = data.get('input')
                if not isinstance(text, str) or not text.strip():
                    raise UserError('Informe "input" com o texto a falar.')
                return self.reply(200, self.server.speech.synthesize(text.strip()), 'audio/wav')
            if path == '/v1/audio/speech/cancel':
                self.server.speech.cancel()
                return self.reply(200, {'cancelled': True})
        except UserError as exc:
            return self.fail(400, str(exc))
        except json.JSONDecodeError:
            return self.fail(400, 'JSON inválido.')
        self.fail(404, f'Unexpected endpoint or method. (POST {path})')


class Worker(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, data, token=''):
        super().__init__(address, Handler)
        whisper = os.environ.get('FERRIS_WHISPER_MODEL') or str(data/'whisper'/'small')
        self.transcriber = Transcriber(whisper if (Path(whisper)/'model.bin').is_file() else '')
        self.speech = DesignedSpeech(data/'tts'/'qwen-design')
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
        print('Aviso: Whisper ausente. Execute tools/setup_whisper.py.', file=sys.stderr)
    if not worker.speech.ready:
        print('Aviso: voz Qwen ausente. Execute tools/setup_designed_tts.py.', file=sys.stderr)
    if not token and args.host != '127.0.0.1':
        print('Aviso: sem FERRIS_VOICE_TOKEN, qualquer máquina da rede pode usar este worker.', file=sys.stderr)
    if worker.speech.ready:
        # Aquecer em segundo plano: a porta abre na hora e o primeiro pedido não
        # paga o carregamento das sessões ONNX nem a construção do tokenizer.
        threading.Thread(target=worker.speech.warm, daemon=True).start()
    print(f'Worker de voz em http://{args.host}:{args.port}/v1', flush=True)
    try:
        worker.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
