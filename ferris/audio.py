"""WAV validation and optional local transcription; no automatic model downloads."""
import io
import threading
import wave
from .core import UserError


def read_wav(raw, maximum=15):
    try:
        with wave.open(io.BytesIO(raw), 'rb') as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
                raise UserError('Use WAV PCM mono, 16 bits, 16 kHz.')
            n = wav.getnframes()
            if not 1600 <= n <= maximum * 16000:
                raise UserError(f'O áudio deve durar entre 0,1 e {maximum} segundos.')
            pcm = wav.readframes(n)
            if len(pcm) != n * 2:
                raise UserError('O arquivo WAV está incompleto.')
            return pcm
    except (wave.Error, EOFError) as exc:
        raise UserError('Arquivo WAV inválido.') from exc


def read_recording(raw):
    """Validate training audio without rejecting quiet, real ambient recordings."""
    pcm = read_wav(raw, maximum=5)
    if not any(pcm):
        raise UserError('Gravação sem sinal de áudio. Confira o mute do sistema e o microfone selecionado no navegador; depois grave novamente.')
    return pcm


class Transcriber:
    def __init__(self, model_path=''):
        self.path = model_path
        self.model = None
        self.lock = threading.Lock()

    def transcribe(self, raw):
        read_wav(raw)
        if not self.path:
            raise UserError('Para transcrição local, instale o extra voice e configure FERRIS_WHISPER_MODEL com um diretório de modelo local.')
        if not self.lock.acquire(blocking=False):
            raise UserError('A transcrição está ocupada. Tente novamente.')
        try:
            if self.model is None:
                from faster_whisper import WhisperModel
                self.model = WhisperModel(self.path, device='cpu', compute_type='int8', cpu_threads=4,
                                          num_workers=1, local_files_only=True)
            segments, _ = self.model.transcribe(io.BytesIO(raw), language='pt', beam_size=3,
                                                vad_filter=True, condition_on_previous_text=False)
            return ' '.join(s.text.strip() for s in segments).strip()
        except ImportError as exc:
            raise UserError('Instale as dependências de voz: pip install -e ".[voice]".') from exc
        except (OSError, RuntimeError, ValueError) as exc:
            raise UserError('Falha ao carregar/transcrever com o modelo local Whisper.') from exc
        finally:
            self.lock.release()
