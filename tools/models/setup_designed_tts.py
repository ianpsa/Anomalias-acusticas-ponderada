"""Download the pinned CPU INT4 expressive voice (about 1.75 GB). No inference API."""
import argparse
import hashlib
from pathlib import Path
from urllib.request import urlopen

ASSETS = {
    "cpu_int4/code_predictor.onnx": [
        "https://huggingface.co/onnx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign/resolve/f7d919ae545c1410acbc36d3c9d849c47f66ed76/cpu_int4/code_predictor.onnx",
        "8ed3192f9e1ee202d58c48520766c654e7dee1fb6bef68991b004d30b339bded"
    ],
    "cpu_int4/codec_embed.onnx": [
        "https://huggingface.co/onnx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign/resolve/f7d919ae545c1410acbc36d3c9d849c47f66ed76/cpu_int4/codec_embed.onnx",
        "ff9ab8ff7af685a7f2bc846e02f939abec6c911e03a7e1a7101e93b30b3cfbfc"
    ],
    "cpu_int4/manifest.json": [
        "https://huggingface.co/onnx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign/resolve/f7d919ae545c1410acbc36d3c9d849c47f66ed76/cpu_int4/manifest.json",
        "5e39e3d5d17612805b462250fb7b7cff47f5c2dd959a38655917696a8b122f58"
    ],
    "cpu_int4/residual_embed.onnx": [
        "https://huggingface.co/onnx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign/resolve/f7d919ae545c1410acbc36d3c9d849c47f66ed76/cpu_int4/residual_embed.onnx",
        "8276e02fa4d460dc1d702db3f156985ccb1286a3eac501225aa9c2c0ab0b1c57"
    ],
    "cpu_int4/talker_cache.onnx": [
        "https://huggingface.co/onnx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign/resolve/f7d919ae545c1410acbc36d3c9d849c47f66ed76/cpu_int4/talker_cache.onnx",
        "1a444cdfbb36267f3bb929f5a1dec16ac1d680b2b133b952937d67bf2481b63b"
    ],
    "cpu_int4/text_embed.onnx": [
        "https://huggingface.co/onnx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign/resolve/f7d919ae545c1410acbc36d3c9d849c47f66ed76/cpu_int4/text_embed.onnx",
        "827ccda80535e7f74d7a6ca52cd2d36325e65b17b19dd4bd42ff6a5ae3cc34ec"
    ],
    "cpu_int4/tok_decoder.onnx": [
        "https://huggingface.co/onnx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign/resolve/f7d919ae545c1410acbc36d3c9d849c47f66ed76/cpu_int4/tok_decoder.onnx",
        "8ec10051735029f6e08b04834128c9108428885c39384f443e49a6790ccb129f"
    ],
    "config.json": [
        "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign/resolve/5ecdb67327fd37bb2e042aab12ff7391903235d3/config.json",
        "aecd2cc4c1fe9edef1cb7ca7c401685a43879ad43f3f9e883f1c6760b61731e0"
    ],
    "tokenizer_config.json": [
        "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign/resolve/5ecdb67327fd37bb2e042aab12ff7391903235d3/tokenizer_config.json",
        "dc3c31c3bdaedd5016382bb3cbe07323026775ad51f5a4fb564505992ae4a670"
    ],
    "vocab.json": [
        "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign/resolve/5ecdb67327fd37bb2e042aab12ff7391903235d3/vocab.json",
        "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"
    ],
    "merges.txt": [
        "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign/resolve/5ecdb67327fd37bb2e042aab12ff7391903235d3/merges.txt",
        "599bab54075088774b1733fde865d5bd747cbcc7a547c5bc12610e874e26f5e3"
    ]
}


def setup(output):
    for name, (url, expected) in ASSETS.items():
        target = output/name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            with target.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() == expected:
                    continue
        partial = target.with_suffix(target.suffix + '.part')
        try:
            print(f'Baixando {name}…', flush=True)
            sha = hashlib.sha256()
            with urlopen(url, timeout=60) as response, partial.open('wb') as stream:
                while chunk := response.read(1024*1024):
                    sha.update(chunk); stream.write(chunk)
            if sha.hexdigest() != expected:
                raise ValueError('Falha na verificação SHA-256: ' + name)
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
    print('Voz expressiva instalada. Reinicie o Ferris; a primeira síntese pode levar cerca de um minuto em CPU.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[2]/'data/tts/qwen-design')
    setup(parser.parse_args().output)
