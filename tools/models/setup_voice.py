"""Prepare persistent voice assets before starting the Compose voice service."""
from pathlib import Path

from tools.models.setup_designed_tts import setup


def main():
    root = Path(__file__).resolve().parents[2] / 'data'
    whisper = root / 'whisper/small'
    required = ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')
    if not all((whisper / name).is_file() for name in required):
        from faster_whisper.utils import download_model
        print('Baixando Whisper small…', flush=True)
        download_model('small', output_dir=str(whisper))
    setup(root / 'tts/qwen-design')


if __name__ == '__main__':
    main()
