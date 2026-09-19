import base64
import binascii
import io
import tempfile
import threading
import unittest
import wave
from pathlib import Path

from ferris.core import Assistant, Settings, UserError
from ferris.device import Device


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.assistant = Assistant(Settings(Path(self.temp.name)))
        self.device = Device('', self.assistant)
        self.device.connected = True
        self.started = threading.Event()
        self.writes = []
        test = self
        class Stream:
            def write(self, data):
                test.writes.append(data)
                test.started.set()
        self.device.stream = Stream()
        self.state(False, 0)

    def tearDown(self): self.temp.cleanup()

    def state(self, muted, generation, boot='aabbccdd'):
        return self.assistant.update_hardware(dict(device='esp32-ferris', muted=muted,
                    capture_active=not muted, generation=generation, boot_id=boot))

    def begin(self):
        self.result = {}
        def run():
            try: self.result['wav'] = self.device.record()
            except UserError as error: self.result['error'] = str(error)
        self.worker = threading.Thread(target=run); self.worker.start()
        self.assertTrue(self.started.wait(2))
        self.identifier = self.writes[0].strip().split()[1]
        self.device._audio(b'FERRIS_RECORD_BEGIN ' + self.identifier)

    def frame(self, sequence, pcm):
        self.device._audio(b'FERRIS_AUDIO ' + self.identifier + b' ' + str(sequence).encode() + b' ' + base64.b64encode(pcm))

    def test_real_protocol_roundtrip_and_checksum(self):
        self.begin()
        pcm = b'\x01\x02' * 32000
        for i in range(80): self.frame(i, pcm[i*800:(i+1)*800])
        self.device._audio(b'FERRIS_AUDIO_END ' + self.identifier + f' 64000 {binascii.crc32(pcm):08x}'.encode())
        self.worker.join(2)
        with wave.open(io.BytesIO(self.result['wav'])) as wav:
            self.assertEqual((wav.getframerate(), wav.getnframes()), (16000, 32000))
            self.assertEqual(wav.readframes(32000), pcm)

    def test_missing_frame_is_rejected(self):
        self.begin(); self.frame(1, bytes(800)); self.worker.join(2)
        self.assertIn('error', self.result)
        self.assertNotIn('wav', self.result)

    def test_mute_cancels_partial_audio_and_ignores_delayed_state(self):
        self.begin(); self.frame(0, bytes(800))
        muted = self.state(True, 1)
        self.device.cancel_recording(); self.worker.join(2)
        self.assertIn('error', self.result)
        self.assertEqual(self.state(False, 0), muted)
        with self.assertRaises(UserError): self.assistant.wake('esp32-ferris')
        with self.assertRaises(UserError): self.device.record()
        self.state(False, 0, '11223344')
        self.assertEqual(self.state(True, 9)['boot_id'], '11223344')

    def test_short_transfer_rejected_even_with_matching_crc(self):
        self.begin(); self.frame(0, bytes(800))
        self.device._audio(b'FERRIS_AUDIO_END ' + self.identifier + f' 800 {binascii.crc32(bytes(800)):08x}'.encode())
        self.worker.join(2); self.assertIn('error', self.result)
