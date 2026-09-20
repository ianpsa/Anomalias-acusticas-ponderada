"""Gera exemplos do aumento de dados para ouvir, sem tocar nas gravações.

O aumento de verdade acontece dentro de tools/training/train_wake.py, aplicado
apenas ao conjunto de treino. Gravar arquivos aumentados dentro de
data/recordings colocaria cópias quase idênticas em validação e teste, e as
métricas passariam a medir o que o modelo já viu.

Use este script só para conferir de ouvido se as transformações continuam
soando como a classe delas.
"""
import argparse
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.training import augment

WINDOW = augment.WINDOW


def read(path):
    with wave.open(str(path), 'rb') as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2 or handle.getframerate() != 16000:
            raise ValueError(f'{path}: use WAV mono, 16 bits, 16 kHz.')
        return handle.readframes(handle.getnframes())


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(raw)


def centre(raw):
    """Mesma janela de 1 segundo que o treino usa: a região de maior energia."""
    audio = np.frombuffer(raw, dtype='<i2')
    if len(audio) < WINDOW:
        audio = np.pad(audio, (0, WINDOW - len(audio)))
    starts = range(0, len(audio) - WINDOW + 1, 800)
    start = max(starts, key=lambda n: np.square(audio[n:n + WINDOW].astype(float)).sum())
    return audio[start:start + WINDOW].astype('<i2').tobytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=ROOT/'data'/'recordings')
    parser.add_argument('--output', type=Path, default=ROOT/'data'/'augment-preview')
    parser.add_argument('--per-class', type=int, default=2, help='Gravações por classe')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()

    ambient = [read(p) for p in sorted((args.data/'noise').glob('*/*.wav'))]
    if not ambient:
        print('Sem gravações de ambiente: as misturas vão sair sem fundo.', file=sys.stderr)
    rng = np.random.default_rng(args.seed)
    total = 0
    for category in ('ferris', 'other', 'noise'):
        paths = sorted((args.data/category).glob('*/*.wav'))[:args.per_class]
        if not paths:
            print(f'{category}: nenhuma gravação.', file=sys.stderr)
            continue
        for path in paths:
            window = centre(read(path))
            write(args.output/category/f'{path.stem}-original.wav', window)
            if category == 'ferris':
                groups = [('positivo', augment.positive_variants(window, rng, ambient)),
                          ('negativo-parcial', augment.partial_word_negatives(window, rng, ambient))]
            elif category == 'other':
                groups = [('negativo', augment.speech_negative_variants(window, rng, ambient))]
            else:
                groups = [('ambiente', augment.ambient_variants(window, rng, ambient))]
            for kind, items in groups:
                for index, item in enumerate(items, 1):
                    write(args.output/category/f'{path.stem}-{kind}-{index}.wav', item)
                    total += 1
    print(f'{total} exemplos em {args.output}. As gravações originais não foram alteradas.')


if __name__ == '__main__':
    main()
