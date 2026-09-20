import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import wave

import numpy as np

from ferris.core import UserError
from ferris.designed_speech import DesignedSpeech


class DesignedSpeechTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.voice = DesignedSpeech(self.temp.name)
        self.pipe = Mock()
        self.pipe.generate.return_value = np.ones((5,16), dtype=np.int64)
        self.pipe.decode_chunked.return_value = np.ones((1,1,2400), dtype=np.float32)*.1
        self.voice.pipe = self.pipe

    def tearDown(self): self.temp.cleanup()

    def test_native_wav_and_persistent_cache(self):
        raw = self.voice.synthesize('Olá! Como está seu dia?')
        with wave.open(io.BytesIO(raw)) as wav:
            self.assertEqual((wav.getframerate(),wav.getnframes()), (24000,2400))
        # Recreating the engine should serve exactly the same voice preview offline.
        fresh = DesignedSpeech(self.temp.name)
        self.assertEqual(fresh.synthesize('Olá! Como está seu dia?'), raw)
        self.assertIsNone(fresh.pipe)

    def test_mute_cancels_before_decoding_or_caching(self):
        def generate(*args, **kwargs):
            self.voice.cancel()
            return np.ones((5,16), dtype=np.int64)
        self.pipe.generate.side_effect = generate
        with self.assertRaisesRegex(UserError,'cancelada'):
            self.voice.synthesize('Uma resposta cancelada.')
        self.pipe.decode_chunked.assert_not_called()
        self.assertFalse(list(Path(self.temp.name).rglob('*.wav')))

    def test_generation_limit_never_returns_cut_off_speech(self):
        self.pipe.generate.return_value = np.ones((250,16), dtype=np.int64)
        with self.assertRaisesRegex(UserError,'não concluiu'):
            self.voice.synthesize('Não devolva áudio truncado.')
        self.pipe.decode_chunked.assert_not_called()

    def test_chunking_preserves_all_words(self):
        text = ('Um exemplo de frase longa em português. '*12).strip()
        self.voice.synthesize(text)
        chunks = [call.args[0] for call in self.pipe.generate.call_args_list]
        self.assertEqual(' '.join(chunks), text)
        self.assertTrue(all(len(chunk)<=140 for chunk in chunks))
