"""Download Whisper once; transcription then uses local files only."""
import argparse
from pathlib import Path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[2]/'data/whisper/small')
    args = parser.parse_args()
    from faster_whisper.utils import download_model
    download_model('small', output_dir=str(args.output))
    print(f'Whisper small disponível em {args.output}. Reinicie o Ferris para habilitar a voz local.')
