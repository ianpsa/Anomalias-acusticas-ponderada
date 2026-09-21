import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib import error, request
import wave

from ferris.core import Settings, UserError
from ferris.designed_speech import FILES
from ferris.remote_voice import RemoteSpeech, RemoteTranscriber
from ferris.server import Server
from ferris.voice_worker import Worker, read_upload


def wav(rate=16000):
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        w.writeframes(b'\x01\x00' * (rate // 5))
    return out.getvalue()


class RemoteVoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.worker = Worker(('127.0.0.1', 0), self.root, 'voice-secret')
        for name in FILES:
            path = self.worker.speech.designed.folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        self.worker.transcriber.path = 'fixture-whisper'
        self.worker.transcriber.transcribe = Mock(return_value='Bom dia!')
        self.worker.speech.designed.synthesize = Mock(return_value=wav(24000))
        self.thread = threading.Thread(target=self.worker.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.worker.server_port}'
        self.speech = RemoteSpeech(self.base, 'voice-secret')

    def tearDown(self):
        self.worker.shutdown(); self.worker.server_close(); self.thread.join()
        self.temp.cleanup()

    def post(self, path, data):
        req = request.Request(self.base+path, data=json.dumps(data).encode(),
                              headers={'Content-Type': 'application/json', 'Authorization': 'Bearer voice-secret'})
        try:
            with request.urlopen(req, timeout=5) as response:
                return response.status, response.read()
        except error.HTTPError as exc:
            return exc.code, exc.read()

    def test_status_requires_real_worker_health_and_auth(self):
        self.assertFalse(RemoteSpeech(self.base, 'wrong').status()['ready'])
        status = self.speech.status()
        self.assertTrue(status['ready']); self.assertTrue(status['transcription_ready'])
        self.assertFalse(status['designed_preview_ready'])
        self.assertNotIn('voice-secret', json.dumps(status))
        with patch('ferris.remote_voice._call', return_value=(b'{"data":[]}', 'application/json')):
            status = RemoteSpeech(self.base).status()
            self.assertFalse(status['ready'])
            self.assertIn('Health check', status['error'])

    def test_real_http_transcription_and_speech_roundtrip(self):
        raw = wav()
        self.assertEqual(RemoteTranscriber(self.base, 'voice-secret').transcribe(raw), 'Bom dia!')
        self.worker.transcriber.transcribe.assert_called_once_with(raw)
        self.assertEqual(self.speech.synthesize('**Olá!**', request_id='question-1'), wav(24000))
        self.worker.speech.designed.synthesize.assert_called_once_with('Olá!')

    def test_worker_rejects_concurrent_generation_and_scopes_cancel(self):
        started, release = threading.Event(), threading.Event()
        results = []
        def render(text):
            started.set()
            if not release.wait(5): raise RuntimeError('Test timed out')
            return wav(24000)
        self.worker.speech.designed.synthesize.side_effect = render
        thread = threading.Thread(target=lambda: results.append(self.post('/v1/audio/speech',
                                   {'input':'Oi', 'request_id':'current'})))
        thread.start()
        try:
            self.assertTrue(started.wait(3))
            status, body = self.post('/v1/audio/speech', {'input':'Outra', 'request_id':'second'})
            self.assertEqual(status, 400); self.assertIn('preparada', body.decode())
            self.post('/v1/audio/speech/cancel', {'request_id':'previous'})
            self.assertFalse(self.worker.speech.designed.cancel_event.is_set())
            self.post('/v1/audio/speech/cancel', {'request_id':'current'})
            self.assertTrue(self.worker.speech.designed.cancel_event.is_set())
        finally:
            release.set(); thread.join(5)
        self.assertEqual(results[0][0], 200)  # Mock engine; real ONNX checks cancel_event.

    def test_client_discards_cancelled_audio_even_with_older_worker(self):
        started, release = threading.Event(), threading.Event()
        results = []
        def render(text):
            started.set(); release.wait(5); return wav(24000)
        self.worker.speech.designed.synthesize.side_effect = render
        def run():
            try: results.append(self.speech.synthesize('Olá', request_id='current'))
            except UserError as exc: results.append(str(exc))
        thread = threading.Thread(target=run); thread.start()
        try:
            self.assertTrue(started.wait(3))
            with self.assertRaisesRegex(UserError, 'preparada'):
                self.speech.synthesize('Outra', request_id='other')
            self.speech.cancel('previous')
            self.assertFalse(self.worker.speech.designed.cancel_event.is_set())
            self.speech.cancel('current')
        finally:
            release.set(); thread.join(5)
        self.assertEqual(results, ['Síntese cancelada.'])
        self.assertIsNone(self.speech.request_id)

    def test_invalid_payloads_and_audio_return_controlled_errors(self):
        for data in ([], {'input':'a'*2001}, {'input':'Oi', 'voice':'M1'}, {'input':'Oi', 'request_id':{}}):
            self.assertEqual(self.post('/v1/audio/speech', data)[0], 400)
        self.assertEqual(self.post('/v1/audio/speech/cancel', {})[0], 400)
        for body in (b'[]', b'{"text": 42}', b'{}'):
            with patch('ferris.remote_voice._call', return_value=(body, 'application/json')):
                with self.assertRaises(UserError): RemoteTranscriber(self.base).transcribe(wav())
        with patch('ferris.remote_voice._call', return_value=(b'not-wave', 'audio/wav')):
            with self.assertRaisesRegex(UserError, 'WAV inválido'): self.speech.synthesize('Oi')

    def test_multipart_preserves_binary_audio(self):
        raw = wav() + b'\r\n'
        body = b'--fixture\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n' + raw + b'\r\n--fixture--\r\n'
        self.assertEqual(read_upload(body, 'multipart/form-data; boundary="fixture"'), raw)
        with self.assertRaises(UserError): read_upload(b'invalid', 'multipart/form-data')

    def test_saved_connection_survives_restart_and_never_loads_local_models(self):
        root = self.root/'panel'
        settings = Settings(root)
        settings.update({'voice_url': self.base, 'voice_token':'voice-secret'})
        self.assertNotIn('voice-secret', json.dumps(settings.public()))
        with patch('ferris.server.Transcriber', side_effect=AssertionError('local Whisper instantiated')), \
             patch('ferris.server.Speech', side_effect=AssertionError('local TTS instantiated')):
            with Server(('127.0.0.1',0), root) as server:
                self.assertIsInstance(server.transcriber, RemoteTranscriber)
                self.assertTrue(server.speech.status()['ready'])
                self.assertEqual(server.settings.public()['voice_url'], self.base+'/v1')
                server.settings.update({'name':'Ferris friend'})
                original = server.speech; server.configure_voice()
                self.assertIs(server.speech, original)
        with self.assertRaises(UserError): settings.update({'voice_url':'file:///bad'})
        self.assertEqual(Settings(root).get()['voice_url'], self.base+'/v1')
