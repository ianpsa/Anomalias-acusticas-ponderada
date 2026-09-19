"""Build the checked-in ESP-IDF project and flash a local USB ESP32 using Docker."""
import argparse
import os
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parent.parent


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    args = parser.parse_args()
    port = Path(args.port).resolve(strict=True)
    if not port.is_char_device() or port.parent != Path('/dev'):
        parser.error('Selecione uma porta serial local em /dev.')
    build = ROOT/'build/esp32-upload'; build.mkdir(parents=True, exist_ok=True)
    container = 'ferris-flash-'+uuid.uuid4().hex
    try:
        subprocess.run(['docker','run','--rm','--name',container,'--user',f'{os.getuid()}:{os.getgid()}',
                    '--device',str(port), '-e',f'FERRIS_FLASH_PORT={port}',
                    '-v',f'{ROOT / "firmware"}:/source:ro','-v',f'{build}:/workspace',
                    '-w','/workspace','espressif/idf:v5.4.2','bash','-lc',
                        'cp -a /source/. /workspace/ && idf.py -p "$FERRIS_FLASH_PORT" -b 460800 flash'], check=True, timeout=540)
    finally:
        subprocess.run(['docker','rm','-f',container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
