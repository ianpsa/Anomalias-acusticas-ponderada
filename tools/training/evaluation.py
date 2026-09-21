"""Replay clips with the same one-second window and two-hit rule as the ESP32."""
import numpy as np


def stream_windows(pcm):
    audio = np.frombuffer(pcm, dtype='<i2')
    # Silence around the clip lets short recordings cross window boundaries.
    # Keep the microphone DC level so padding does not create artificial clicks.
    audio = np.pad(audio, (8000, 8000), constant_values=int(audio.mean()))
    return [audio[i:i+16000].astype('<i2').tobytes()
            for i in range(0, len(audio)-15999, 4000)]


def confirmation_score(probabilities):
    values = np.asarray(probabilities)
    return float(np.minimum(values[:-1], values[1:]).max()) if len(values) > 1 else 0.0


def choose_threshold(labels, scores, max_false_positive_rate=.025, personal=None):
    from tools.training.train_wake import report
    candidates = []
    for threshold in np.linspace(.05, .99, 95):
        metrics = report(labels, scores, threshold)
        # Thousands of downloaded files must not hide errors on the actual mic.
        local = report(np.asarray(labels)[personal], np.asarray(scores)[personal], threshold) if personal is not None and np.any(personal) else metrics
        rate = max(metrics['false_positive_rate'], local['false_positive_rate'])
        candidates.append((rate > max_false_positive_rate,
                           -metrics['recall'] if rate <= max_false_positive_rate else rate,
                           metrics['false_positive_rate'], float(threshold)))
    return min(candidates)[-1]
