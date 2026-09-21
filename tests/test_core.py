import base64
import io
import json
import tempfile
import threading
import unittest
import wave
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from urllib import error, request

from ferris.audio import read_wav
from ferris.core import Assistant, Settings, UserError, checked_url, greeting
from ferris.server import Server, question_settings


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(Path(self.temp.name))
        self.settings.update({'model':'remote-model','api_key':'secret'})
        self.assistant = Assistant(self.settings)

    def tearDown(self): self.temp.cleanup()

    def test_question_settings_bound_audio_duration_and_reject_invalid_numbers(self):
        with patch.dict('os.environ', {'MIC_THRESHOLD':'nan', 'QUESTION_SILENCE_MS':'bad',
                                       'QUESTION_MAX_SECONDS':'200'}):
            self.assertEqual(question_settings(),dict(threshold=.003,silence_ms=1000,max_seconds=14))
        with patch.dict('os.environ', {'MIC_THRESHOLD':'.002', 'QUESTION_SILENCE_MS':'1500',
                                       'QUESTION_MAX_SECONDS':'10'}):
            self.assertEqual(question_settings(),dict(threshold=.002,silence_ms=1500,max_seconds=10))

    def test_time_greeting_and_midnight(self):
        for hour, expected in [(8,'Bom dia'),(15,'Boa tarde'),(20,'Boa noite'),(1,'madrugada')]:
            self.assertIn(expected,greeting(self.settings.get(),datetime(2026,9,18,hour)))

    def test_urls_and_secret_redaction(self):
        self.assertEqual(checked_url('http://192.168.1.5:1234/'),'http://192.168.1.5:1234/v1')
        for url in ['file:///etc/passwd','http://user:pass@host','http://host/?key=1','http://host:wrong']:
            with self.assertRaises(UserError): checked_url(url)
        self.assertNotIn('secret',json.dumps(self.settings.public()))
        self.assertEqual(self.settings.path.stat().st_mode & 0o777,0o600)

    @patch('ferris.core.fetch_json')
    def test_remote_chat_context_and_bounded_history(self, fetch):
        fetch.return_value={'choices':[{'message':{'content':'Olá!'}}]}
        for _ in range(12): self.assistant.chat('Bom dia','tab1')
        self.assertEqual(len(self.assistant.sessions['tab1']),16)
        url,payload,key=fetch.call_args.args
        self.assertTrue(url.endswith('/v1/chat/completions'))
        self.assertEqual(key,'secret'); self.assertEqual(payload['model'],'remote-model')
        self.assertNotIn('reasoning_effort',payload)
        self.assertIn('Data e hora locais',payload['messages'][0]['content'])
        self.assistant.chat('Nova conversa','tab2')
        self.assertEqual(len(fetch.call_args.args[1]['messages']),2)

    @patch('ferris.core.fetch_json')
    def test_gemma4_voice_reply_disables_thinking(self, fetch):
        fetch.return_value={'choices':[{'message':{'content':'Olá!'}}]}
        for model in ['ferris-gemma','google/gemma-4-e2b','gemma-4-E4B-it']:
            with self.subTest(model=model):
                self.settings.update({'model':model})
                self.assertEqual(self.assistant.chat('Olá','gemma')['text'],'Olá!')
                self.assertEqual(fetch.call_args.args[1]['reasoning_effort'],'none')

    @patch('ferris.core.fetch_json')
    def test_code_rejected_without_calling_model(self,fetch):
        for query in ['Crie um código em Python','Write a shell script','execute um script']:
            self.assertIn('não criar',self.assistant.chat(query,'test')['text'])
        fetch.assert_not_called()

    @patch('ferris.core.fetch_json')
    def test_unconfigured_search_returns_real_link_not_fake_results(self,fetch):
        response=self.assistant.chat('Pesquise café e pão','test')
        self.assertIn('não está configurada',response['text'])
        self.assertTrue(response['sources'][0]['url'].startswith('https://www.google.com/search?'))
        fetch.assert_not_called()

    @patch('ferris.core.fetch_json')
    def test_unknown_tool_cannot_execute(self,fetch):
        self.settings.update({'search_key':'key'})
        fetch.return_value={'choices':[{'message':{'tool_calls':[{'id':'1','function':{'name':'shell','arguments':'{"query":"ls"}'}}]}}]}
        with self.assertRaisesRegex(UserError,'não permitidos'): self.assistant.chat('Olá','test')
        self.assertEqual(fetch.call_count,1)

    @patch('ferris.core.fetch_json')
    def test_tool_roundtrip_with_sources(self,fetch):
        self.settings.update({'search_key':'key'})
        fetch.side_effect=[
            {'choices':[{'message':{'content':None,'tool_calls':[{'id':'a','type':'function','function':{'name':'web_search','arguments':'{"query":"notícias espaço"}'}}]}}]},
            {'organic_results':[{'title':'Fonte','link':'https://example.org/news','snippet':'Uma notícia.'}]},
            {'choices':[{'message':{'content':'Encontrei uma notícia.'}}]}]
        result=self.assistant.chat('Quais são as novidades?','test')
        self.assertEqual(result['sources'][0]['url'],'https://example.org/news')
        self.assertEqual(fetch.call_args.args[1]['messages'][-1]['role'],'tool')

    def test_failed_validation_keeps_settings(self):
        previous=self.settings.get()
        with self.assertRaises(UserError): self.settings.update({'timezone':'bad/zone','model':'oops'})
        self.assertEqual(previous,self.settings.get())

    def test_wav_validation(self):
        buff=io.BytesIO()
        with wave.open(buff,'wb') as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000); f.writeframes(b'\0'*32000)
        self.assertEqual(len(read_wav(buff.getvalue())),32000)
        with self.assertRaises(UserError): read_wav(buff.getvalue()[:-2])
        with self.assertRaises(UserError): read_wav(b'not a wav')


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.server=Server(('127.0.0.1',0),Path(cls.temp.name),device_token='device-secret')
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True); cls.thread.start()
        cls.url='http://127.0.0.1:'+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.temp.cleanup()

    def call(self,path,body=None,headers=None):
        data=json.dumps(body).encode() if body is not None else None
        req=request.Request(self.url+path,data=data,headers={'Content-Type':'application/json',**(headers or {})})
        try:
            with request.urlopen(req) as response: return response.status,response.read()
        except error.HTTPError as e: return e.code,e.read()

    def test_static_and_status(self):
        status,body=self.call('/'); self.assertEqual(status,200); self.assertIn(b'Pode falar',body)
        status,body=self.call('/api/status'); self.assertEqual(status,200); self.assertIn('recordings',json.loads(body))

    def test_cross_origin_denied(self):
        status,_=self.call('/api/settings',{'model':'bad'},{'Origin':'http://evil.example'})
        self.assertEqual(status,403)
        status,_=self.call('/api/train',{'split_mode':'recordings'},{'Origin':'http://evil.example'})
        self.assertEqual(status,403)

    def test_training_endpoint_requires_data_and_valid_mode(self):
        status,_=self.call('/api/train',{'split_mode':'shell'})
        self.assertEqual(status,400)
        status,_=self.call('/api/train',{'split_mode':'recordings'})
        self.assertEqual(status,400)
        status,body=self.call('/api/training')
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['state'],'idle')

    def test_recordings_reject_muted_input_but_accept_quiet_ambience(self):
        def audio(pcm):
            buff=io.BytesIO()
            with wave.open(buff,'wb') as f:
                f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000); f.writeframes(pcm)
            return base64.b64encode(buff.getvalue()).decode()
        folder=Path(self.temp.name)/'recordings'
        before=set(folder.glob('*/*/*.wav'))
        for label in ['ferris','other','noise']:
            status,body=self.call('/api/recordings',{'label':label,'group':'muted','audio':audio(b'\0'*64000)})
            self.assertEqual(status,400)
            self.assertIn('sem sinal',json.loads(body)['error'])
        self.assertEqual(set(folder.glob('*/*/*.wav')),before)
        status,_=self.call('/api/recordings',{'label':'noise','group':'quiet','audio':audio(b'\x01\x00'*32000)})
        self.assertEqual(status,201)

    def test_device_token_and_event(self):
        status,_=self.call('/api/device/wake',{'device':'esp32'})
        self.assertEqual(status,401)
        payload={'device':'esp32','confidence':.9,'event_id':'boot-123'}
        status,body=self.call('/api/device/wake',payload,{'Authorization':'Bearer device-secret'})
        self.assertEqual(status,200); self.assertEqual(json.loads(body)['device'],'esp32')
        status,duplicate=self.call('/api/device/wake',payload,{'Authorization':'Bearer device-secret'})
        self.assertEqual(status,200); self.assertEqual(json.loads(duplicate)['id'],json.loads(body)['id'])

    def test_physical_mute_blocks_audio_and_discards_inflight_results(self):
        assistant = self.server.assistant
        old = assistant.hardware_state()
        payload = dict(device='esp32-ferris', type='state', boot_id='abcdef12',
                       generation=1, muted=True, capture_active=False)
        headers = {'Authorization':'Bearer device-secret'}
        try:
            self.assertEqual(self.call('/api/device/wake', payload, headers)[0], 200)
            for path in ['/api/wake', '/api/speech', '/api/transcribe', '/api/detect', '/api/recordings/esp']:
                self.assertEqual(self.call(path, {})[0], 400)
            self.assertTrue(json.loads(self.call('/api/events')[1])['hardware']['muted'])
            payload.update(generation=2, muted=False, capture_active=True)
            self.call('/api/device/wake', payload, headers)
            def interrupted(*args):
                assistant.update_hardware(dict(payload, generation=3, muted=True, capture_active=False))
                assistant.update_hardware(dict(payload, generation=4))
                return 'stale result'
            with patch.object(self.server.transcriber, 'transcribe', side_effect=interrupted):
                status, body = self.call('/api/transcribe', {'audio':''})
                self.assertEqual(status, 400)
                self.assertIn('descartada', json.loads(body)['error'])
        finally:
            with assistant.lock:
                assistant.hardware = old
                assistant.retired_boots.clear()

    def test_esp_recording_saved_and_validated_without_browser_audio(self):
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wav:
            wav.setparams((1,2,16000,0,'NONE','not compressed'))
            wav.writeframes(b'\x01\x00'*32000)
        with patch.object(self.server.device, 'record', return_value=buffer.getvalue()) as record:
            self.assertEqual(self.call('/api/recordings/esp', {'label':'noise','group':'../bad'})[0],400)
            record.assert_not_called()
            status, body = self.call('/api/recordings/esp', {'label':'noise','group':'esp-test'})
            self.assertEqual(status,201)
            name = json.loads(body)['file']; self.assertTrue(name.startswith('esp32-'))
            self.assertEqual((Path(self.temp.name)/'recordings/noise/esp-test'/name).read_bytes(),buffer.getvalue())

    def test_bad_input_and_traversal(self):
        status,_=self.call('/api/chat',{'text':'hi','session':'../bad'}); self.assertEqual(status,400)
        status,_=self.call('/api/device/wake',{'confidence':float('nan')},{'Authorization':'Bearer device-secret'}); self.assertEqual(status,400)
        status,_=self.call('/../data/settings.json'); self.assertEqual(status,404)

    def test_credential_role_separation(self):
        self.server.token='admin-secret'
        try:
            status,_=self.call('/api/settings',{'model':'bad'},{'Authorization':'Bearer device-secret'})
            self.assertEqual(status,401)
            status,_=self.call('/api/status',headers={'Authorization':'Bearer admin-secret'})
            self.assertEqual(status,200)
        finally: self.server.token=''


if __name__=='__main__': unittest.main()
