import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import wave

import numpy as np

from ferris.core import UserError
from ferris.speech import MODEL_FILES, VOICES, Speech


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.speech = Speech(self.temp.name)
        for filename in (*MODEL_FILES, *(f'voice_styles/{v}.json' for v in VOICES)):
            path = Path(self.temp.name)/filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        self.engine = Mock(sample_rate=44100)
        self.engine.get_voice_style.side_effect = lambda name: name
        self.engine.synthesize.return_value = (np.asarray([[0, .5, -.5]], dtype=np.float32), .01)
        self.speech.engine = self.engine

    def tearDown(self):
        self.temp.cleanup()

    def test_native_pitch_and_separate_voice_cache(self):
        first = self.speech.synthesize('Olá, Ferris!', 'M1')
        with wave.open(io.BytesIO(first)) as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (44100, 1, 2))
            self.assertEqual(wav.readframes(3), np.asarray([0,16383,-16383], dtype='<i2').tobytes())
        self.assertEqual(self.speech.synthesize('Olá, Ferris!', 'M1'), first)
        self.assertEqual(self.engine.synthesize.call_count, 1)
        self.speech.synthesize('Olá, Ferris!', 'M3')
        self.assertEqual(self.engine.synthesize.call_count, 2)
        self.assertEqual(self.engine.synthesize.call_args.kwargs['voice_style'], 'M3')
        self.assertEqual(self.engine.synthesize.call_args.kwargs['lang'], 'pt')

    def test_invalid_voice_and_invalid_audio_are_rejected(self):
        for voice in ('../voice', 'soft', None, {}):
            with self.assertRaises(UserError): self.speech.synthesize('Olá', voice)
        self.engine.synthesize.assert_not_called()
        self.engine.synthesize.return_value = (np.asarray([[np.nan]]), 1)
        with self.assertRaises(UserError): self.speech.synthesize('Olá', 'M1')
        self.assertFalse(self.speech.lock.locked())
        self.assertFalse(self.speech.cache)

    def test_late_cancel_cannot_interrupt_a_new_request(self):
        self.speech.request_id = 'current-request'
        self.speech.designed.cancel = Mock()
        self.speech.cancel('previous-request')
        self.speech.designed.cancel.assert_not_called()
        self.speech.cancel('current-request')
        self.speech.designed.cancel.assert_called_once()

    def test_missing_profile_is_reported_before_loading(self):
        (Path(self.temp.name)/'voice_styles/M5.json').unlink()
        self.assertFalse(self.speech.status()['ready'])
        with self.assertRaisesRegex(UserError, 'setup_tts'):
            self.speech.synthesize('Olá', 'M1')
        self.engine.synthesize.assert_not_called()
