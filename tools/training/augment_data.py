"""Light data augmentation - mix into session directories."""
import argparse
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

AUGMENTATIONS = ["noise", "shift", "speed", "volume", "mix"]


def read_wav(path):
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return pcm, w.getframerate()


def write_wav(path, pcm, rate=16000):
    pcm_int16 = np.clip(pcm * 32767, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm_int16.tobytes())


def add_gaussian_noise(pcm, snr_db=25):
    signal_power = np.mean(pcm ** 2)
    noise_power = signal_power / (10 ** (snr_db / 10))
    noise = np.random.normal(0, np.sqrt(noise_power), len(pcm))
    return pcm + noise


def time_shift(pcm, max_shift=0.2):
    shift_samples = int(np.random.uniform(-max_shift, max_shift) * 16000)
    out = np.zeros_like(pcm)
    if shift_samples > 0 and shift_samples < len(pcm):
        out[shift_samples:] = pcm[:-shift_samples]
    elif shift_samples < 0 and -shift_samples < len(pcm):
        out[:shift_samples] = pcm[-shift_samples:]
    else:
        out = pcm.copy()
    return out


def speed_perturbation(pcm, factor_range=(0.95, 1.05)):
    factor = np.random.uniform(*factor_range)
    new_len = int(len(pcm) / factor)
    indices = np.round(np.arange(new_len) * factor).astype(int)
    indices = np.clip(indices, 0, len(pcm) - 1)
    return pcm[indices]


def volume_scaling(pcm, gain_range=(0.8, 1.2)):
    return pcm * np.random.uniform(*gain_range)


def mix_with_noise(pcm, noise_dir, snr_db=20):
    noise_files = list(noise_dir.glob("*.wav"))
    if not noise_files:
        return pcm
    noise_path = noise_files[np.random.randint(len(noise_files))]
    with wave.open(str(noise_path), "rb") as w:
        noise = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if len(noise) != len(pcm):
        if len(noise) > len(pcm):
            noise = noise[:len(pcm)]
        else:
            noise = np.concatenate([noise] * (len(pcm) // len(noise) + 1))[:len(pcm)]
    noise_power = np.mean(noise ** 2)
    signal_power = np.mean(pcm ** 2)
    scale = np.sqrt(signal_power / (noise_power * (10 ** (snr_db / 10))))
    return pcm + noise * scale


def apply_augmentations(pcm, aug_types, noise_dir, rate=16000):
    augmented = pcm.copy()
    for aug in aug_types:
        try:
            if aug == "noise":
                augmented = add_gaussian_noise(augmented, np.random.choice([25, 30]))
            elif aug == "shift":
                augmented = time_shift(augmented)
            elif aug == "speed":
                augmented = speed_perturbation(augmented)
            elif aug == "volume":
                augmented = volume_scaling(augmented)
            elif aug == "mix":
                augmented = mix_with_noise(augmented, noise_dir)
        except Exception:
            pass
    return np.clip(augmented, -1.0, 1.0)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=ROOT / "data" / "recordings")
    p.add_argument("--multiplier", type=int, default=4, help="Augmented copies per original")
    args = p.parse_args()

    data = args.data
    noise_dir = data / "noise"
    manifest = {"categories": {}}

    total_augmented = 0
    for cat in ["ferris", "other", "noise"]:
        cat_dir = data / cat
        sessions = [d for d in cat_dir.iterdir() if d.is_dir()]
        cat_count = 0
        print(f"\n=== {cat}: {len(sessions)} sessions ===")
        for session in sessions:
            wav_files = sorted(session.glob("*.wav"))
            if not wav_files:
                continue
            print(f"  {session.name}: {len(wav_files)} files")
            for wav_path in wav_files:
                try:
                    pcm, rate = read_wav(wav_path)
                except Exception:
                    continue
                for i in range(args.multiplier):
                    aug_types = np.random.choice(
                        AUGMENTATIONS, size=np.random.randint(1, 4), replace=False
                    ).tolist()
                    aug_pcm = apply_augmentations(pcm, aug_types, noise_dir, rate)
                    stem = wav_path.stem
                    aug_name = f"{stem}_aug{i+1}_{'+'.join(aug_types)}.wav"
                    write_wav(session / aug_name, aug_pcm, rate)
                    cat_count += 1
        manifest["categories"][cat] = {"files": cat_count, "sessions": len(sessions)}
        total_augmented += cat_count
        print(f"  -> {cat_count} augmented files")

    manifest["total_augmented"] = total_augmented
    manifest["multiplier"] = args.multiplier
    manifest["augmentations"] = AUGMENTATIONS
    (data.parent / "augmented_manifest.json").write_text(__import__("json").dumps(manifest, indent=2))
    print(f"\nTotal augmented: {total_augmented}")
    print(f"Grand total files: {sum(1 for _ in (data/'ferris').glob('*/*.wav')) + sum(1 for _ in (data/'other').glob('*/*.wav')) + sum(1 for _ in (data/'noise').glob('*/*.wav'))}")


if __name__ == "__main__":
    main()
