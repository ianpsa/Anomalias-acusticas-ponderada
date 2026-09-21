"""Aumento de dados por classe, guiado pelo que cada áudio significa.

As três classes não toleram as mesmas transformações. O que preserva a palavra
"Ferris" não é o que enriquece um ruído de ambiente. Cortes da palavra não viram
negativos automaticamente, porque ainda podem conter o nome reconhecível.

Tudo aqui trabalha sobre janelas de 1 segundo e só é aplicado ao conjunto de
treino. Validação e teste ficam com o áudio original, senão as métricas passam a
medir cópias do que o modelo já viu.

As variações são heurísticas moderadas de volume, ritmo e ambiente. A distância
real também muda a reverberação, portanto estes exemplos não substituem novas
sessões gravadas no microfone da placa.
"""
import numpy as np

WINDOW = 16000
RATE = 16000

# Faixas heurísticas para volume, ritmo e filtragem.
GAIN_FAR = -8.0
GAIN_NEAR = 4.0
ROOM_SNR_MIN = 5.0
ROOM_SNR_MAX = 20.0
SPEED_MIN = 0.93
SPEED_MAX = 1.07
HP_CUT = 60.0
SKIRT = 320.0


def _floats(raw):
    audio = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0
    return audio - audio.mean()


def _bytes(audio):
    # Arredonda e limita antes da conversão para PCM de 16 bits.
    return np.rint(np.clip(audio * 32767.0, -32768.0, 32767.0)).astype('<i2').tobytes()


def _bandlimit(audio):
    """Atenua DC e as bordas do espectro nas variações sintéticas."""
    spectrum = np.fft.rfft(audio)
    freqs = np.fft.rfftfreq(len(audio), 1.0 / RATE)
    hp = np.clip(freqs / HP_CUT, 0.0, 1.0)
    lp = np.clip((RATE / 2 - freqs) / SKIRT, 0.0, 1.0)
    return np.fft.irfft(spectrum * hp * lp, n=len(audio))


def _emit(audio):
    """Filtra a variação e converte para PCM."""
    return _bytes(_bandlimit(np.asarray(audio, dtype=np.float32)))


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
    # Amostras inteiras produziriam aliasing no topo do espectro; interpolacao
    # linear mantém a forma da onda mais perto do que o sensor capturaria.
    index = np.clip(np.arange(int(len(audio) / factor)) * factor, 0, len(audio) - 1)
    stretched = np.interp(index, np.arange(len(audio)), audio)
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
    """Varia posição, ganho e ritmo sem transformar a própria palavra em negativo."""
    audio = _floats(raw)
    energy = float(np.sum((audio - audio.mean()) ** 2))
    out = [audio]
    for samples in (-3200, -2000, -1000, 1000, 2000, 3200):
        kept = audio[:WINDOW-samples] if samples > 0 else audio[-samples:]
        if energy and np.sum((kept-audio.mean()) ** 2) >= .90 * energy:
            out.append(_shift(audio, samples, np.zeros(WINDOW, dtype=np.float32)))
    variants = []
    for item in out:
        variants.append(_emit(item))
        variants.append(_emit(_gain(item, rng, -12, 6)))
        variants.append(_emit(_mix(_gain(item, rng, -8, 2), _ambient(pool, rng), rng, 12, 30)))
    variants.append(_emit(_speed(audio, rng, SPEED_MIN, SPEED_MAX)))
    return variants


def speech_negative_variants(raw, rng, pool):
    """Outras palavras: mesma fala, mesmas condições, rótulo oposto.

    Recebem o mesmo tratamento dos positivos para o modelo não aprender a separar
    as classes pelo nível de ruído ou pelo volume em vez de pelo conteúdo.
    """
    audio = _floats(raw)
    out = [
        _mix(_gain(audio, rng, GAIN_FAR, GAIN_NEAR), _ambient(pool, rng), rng, ROOM_SNR_MIN, ROOM_SNR_MAX),
        _speed(audio, rng, SPEED_MIN, SPEED_MAX),
        _shift(audio, int(rng.integers(-3200, 3200)), np.zeros(WINDOW, dtype=np.float32)),
    ]
    return [_emit(item) for item in out]


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
    return [_emit(item) for item in out]
