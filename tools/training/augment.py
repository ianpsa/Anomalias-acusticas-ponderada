"""Aumento de dados por classe, guiado pelo que cada áudio significa.

As três classes não toleram as mesmas transformações. O que preserva a palavra
"Ferris" não é o que enriquece um ruído de ambiente, e a melhor fonte de negativo
difícil é a própria palavra cortada pela borda da janela.

Tudo aqui trabalha sobre janelas de 1 segundo e só é aplicado ao conjunto de
treino. Validação e teste ficam com o áudio original, senão as métricas passam a
medir cópias do que o modelo já viu.
"""
import numpy as np

WINDOW = 16000
RATE = 16000


def _floats(raw):
    return np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0


def _bytes(audio):
    return np.clip(audio * 32767.0, -32768, 32767).astype('<i2').tobytes()


def _ambient(pool, rng):
    """Um segundo de ambiente real, tirado só das gravações de treino."""
    if not pool:
        return np.zeros(WINDOW, dtype=np.float32)
    audio = _floats(pool[rng.integers(len(pool))])
    if len(audio) < WINDOW:
        audio = np.pad(audio, (0, WINDOW - len(audio)))
    start = int(rng.integers(len(audio) - WINDOW + 1))
    return audio[start:start + WINDOW]


def _gain(audio, rng, low_db, high_db):
    return audio * float(10 ** (rng.uniform(low_db, high_db) / 20))


def _mix(audio, ambient, rng, low_snr, high_snr):
    """Soma ambiente na relação sinal/ruído pedida, em dB."""
    speech = float(np.mean(audio ** 2))
    background = float(np.mean(ambient ** 2))
    if speech <= 0 or background <= 0:
        return audio
    target = float(rng.uniform(low_snr, high_snr))
    scale = np.sqrt(speech / (background * 10 ** (target / 10)))
    return audio + ambient * scale


def _speed(audio, rng, low, high):
    """Muda o ritmo da fala e devolve a janela no mesmo tamanho.

    A faixa é estreita de propósito: acima disso as formantes mudam e a palavra
    deixa de ser a mesma, o que envenenaria o rótulo positivo.
    """
    factor = float(rng.uniform(low, high))
    index = np.clip(np.arange(int(len(audio) / factor)) * factor, 0, len(audio) - 1)
    stretched = audio[index.astype(int)]
    if len(stretched) >= WINDOW:
        start = (len(stretched) - WINDOW) // 2
        return stretched[start:start + WINDOW]
    pad = WINDOW - len(stretched)
    return np.pad(stretched, (pad // 2, pad - pad // 2))


def _shift(audio, samples, fill):
    """Desloca no tempo preenchendo o vazio com ambiente, não com silêncio."""
    out = fill.copy()
    if samples > 0:
        out[samples:] = audio[:WINDOW - samples]
    elif samples < 0:
        out[:samples] = audio[-samples:]
    else:
        out = audio.copy()
    return out


def positive_variants(raw, rng, pool):
    """Palavra de ativação: variar o que muda na vida real, preservar a palavra.

    Distância vira ganho, ambiente vira mistura em SNR variável e ritmo de fala
    vira uma perturbação pequena. O deslocamento é curto para a palavra continuar
    inteira dentro da janela; deslocamento grande é negativo, não positivo.
    """
    audio = _floats(raw)
    out = []
    for samples in (-2400, -1200, 1200, 2400):
        out.append(_shift(audio, samples, _ambient(pool, rng)))
    out.append(_mix(_gain(audio, rng, -8, 4), _ambient(pool, rng), rng, 5, 20))
    out.append(_speed(audio, rng, .93, 1.07))
    return [_bytes(np.clip(item, -1, 1)) for item in out]


def partial_word_negatives(raw, rng, pool):
    """Negativo difícil: a própria palavra cortada pela borda da janela.

    O detector precisa disparar com a palavra inteira, não com o começo dela. Sem
    esses exemplos ele aprende a reagir ao ataque de "Fe" e dispara antes da hora.
    O deslocamento aqui é grande o bastante para sobrar só um pedaço, bem separado
    do deslocamento curto usado nos positivos.
    """
    audio = _floats(raw)
    out = []
    for direction in (-1, 1):
        samples = int(direction * rng.integers(8000, 11200))
        out.append(_shift(audio, samples, _ambient(pool, rng)))
    return [_bytes(np.clip(item, -1, 1)) for item in out]


def speech_negative_variants(raw, rng, pool):
    """Outras palavras: mesma fala, mesmas condições, rótulo oposto.

    Recebem o mesmo tratamento dos positivos para o modelo não aprender a separar
    as classes pelo nível de ruído ou pelo volume em vez de pelo conteúdo.
    """
    audio = _floats(raw)
    out = [
        _mix(_gain(audio, rng, -8, 4), _ambient(pool, rng), rng, 5, 20),
        _speed(audio, rng, .93, 1.07),
        _shift(audio, int(rng.integers(-3200, 3200)), _ambient(pool, rng)),
    ]
    return [_bytes(np.clip(item, -1, 1)) for item in out]


def ambient_variants(raw, rng, pool):
    """Ambiente: a classe que aceita mais liberdade, porque não tem palavra.

    Ganho numa faixa larga cobre desde o silêncio quase mudo até o ambiente alto,
    somar dois ambientes imita cenas reais (ventilador mais TV) e inverter no
    tempo só embaralha o ruído. Nada aqui pode receber fala: viraria um positivo
    com rótulo de ruído.
    """
    audio = _floats(raw)
    out = [
        _gain(audio, rng, -18, 6),
        audio + _ambient(pool, rng) * float(rng.uniform(.3, 1.0)),
        _gain(audio[::-1].copy(), rng, -6, 3),
    ]
    return [_bytes(np.clip(item, -1, 1)) for item in out]
