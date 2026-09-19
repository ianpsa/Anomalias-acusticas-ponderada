"""Download verified Supertonic 3 ONNX assets once; synthesis uses local files only."""
import argparse
import hashlib
from pathlib import Path
from urllib.request import urlopen

BASE = ('https://huggingface.co/supertone-oss-archive/supertonic-3/resolve/'
        'aafc6e32416a594460b32413efc49d7fe4ce6d46/')
ASSETS = {
    "LICENSE": "0d944a9110fed9a9602d60e0423a272903e7bd21ab060490774efc77c2275e9f",
    "config.json": "4099082b107a9d4029849ac76b89eca65e03732660969c2babe5bf308c7357f2",
    "onnx/duration_predictor.onnx": "c3eb91414d5ff8a7a239b7fe9e34e7e2bf8a8140d8375ffb14718b1c639325db",
    "onnx/text_encoder.onnx": "c7befd5ea8c3119769e8a6c1486c4edc6a3bc8365c67621c881bbb774b9902ff",
    "onnx/tts.json": "42078d3aef1cd43ab43021f3c54f47d2d75ceb4e75f627f118890128b06a0d09",
    "onnx/unicode_indexer.json": "9bf7346e43883a81f8645c81224f786d43c5b57f3641f6e7671a7d6c493cb24f",
    "onnx/vector_estimator.onnx": "883ac868ea0275ef0e991524dc64f16b3c0376efd7c320af6b53f5b780d7c61c",
    "onnx/vocoder.onnx": "085de76dd8e8d5836d6ca66826601f615939218f90e519f70ee8a36ed2a4c4ba",
    "voice_styles/M1.json": "e35604687f5d23694b8e91593a93eec0e4eca6c0b02bb8ed69139ab2ea6b0a5b",
    "voice_styles/M2.json": "b76cbf62bac707c710cf0ae5aba5e31eea1a6339a9734bfae33ab98499534a50",
    "voice_styles/M3.json": "ea1ac35ccb91b0d7ecad533a2fbd0eec10c91513d8951e3b25fbba99954e159b",
    "voice_styles/M4.json": "ca8eefad4fcd989c9379032ff3e50738adc547eeb5e221b82593a6d7b3bac303",
    "voice_styles/M5.json": "dd22b92740314321f8ae11c5e87f8dd60d060f15dd3a632b5adf77f471f77af2"
}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def setup(output):
    output.mkdir(parents=True, exist_ok=True)
    for name, expected in ASSETS.items():
        target = output/name
        target.parent.mkdir(parents=True, exist_ok=True)
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
    print(f'Vozes Supertonic 3 disponíveis em {output}. Reinicie o Ferris para habilitá-las.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent.parent/'data/tts/supertonic-3')
    setup(parser.parse_args().output)
