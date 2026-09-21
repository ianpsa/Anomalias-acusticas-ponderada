import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ControlsTests(unittest.TestCase):
    def test_debounce_hold_release_and_stale_audio_after_mute(self):
        component = ROOT / 'firmware/components/ferris_controls'
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'controls-test'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-I' + str(component/'include'), str(component/'ferris_controls.c'),
                            str(ROOT/'tests/controls_test.c'), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)
