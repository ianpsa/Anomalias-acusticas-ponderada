"""Compile only the fixed, checked-in DSP source. Not exposed to the assistant."""
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build():
    folder = ROOT/'build'; folder.mkdir(exist_ok=True)
    source = ROOT/'firmware/components/ferris_dsp'
    output = folder/'libferris_dsp.so'
    fd, tmp = tempfile.mkstemp(dir=folder, suffix='.so'); os.close(fd)
    try:
        subprocess.run([os.getenv('CC', 'cc'), '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-fPIC', '-shared', '-I'+str(source/'include'), str(source/'ferris_dsp.c'),
                        '-lm', '-o', tmp], check=True)
        os.replace(tmp, output)
    finally:
        Path(tmp).unlink(missing_ok=True)
    print(output)
    return output


if __name__ == '__main__':
    build()
