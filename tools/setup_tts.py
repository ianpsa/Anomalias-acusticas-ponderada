"""Download verified Kokoro ONNX assets once; synthesis uses local files only."""
import argparse
import hashlib
from pathlib import Path
from urllib.request import urlopen

BASE = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/'
ASSETS = {
    'kokoro-v1.0.onnx': 'beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a',
    'voices-v1.0.bin': 'bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d',
}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def setup(output):
    output.mkdir(parents=True, exist_ok=True)
    for name, expected in ASSETS.items():
        target = output/name
        if target.is_file() and digest(target) == expected:
            continue
        partial = output/(name+'.part')
        try:
            print(f'Baixando {name}…', flush=True)
            with urlopen(BASE+name, timeout=60) as response, partial.open('wb') as stream:
                while block := response.read(1024*1024):
                    stream.write(block)
            if digest(partial) != expected:
                raise ValueError(f'Hash inválido para {name}. Repita o download.')
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
    print(f'Voz Kokoro disponível em {output}. Reinicie o Ferris para habilitá-la.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent.parent/'data/tts/kokoro')
    setup(parser.parse_args().output)
