"""USB wake events from ESP32; audio and language models remain on the PC."""
import base64
import binascii
import io
import uuid
import wave
import json
import math
from pathlib import Path
import re
import threading
from .core import UserError


def discover_port():
    candidates = sorted(Path('/dev/serial/by-id').glob('*CP210*'))
    return str(candidates[0]) if candidates else ''


def parse_event(line):
    if not line.startswith(b'FERRIS_WAKE ') or len(line) > 2048:
        return None
    try:
        event = json.loads(line[12:])
        if not isinstance(event, dict) or event.get('device') != 'esp32-ferris':
            return None
        if not isinstance(event.get('event_id'), str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', event['event_id']):
            return None
        score = event.get('confidence')
        if type(score) not in (int,float) or not 0 <= score <= 1:
            return None
        metrics = event.get('metrics', {})
        if not isinstance(metrics,dict) or len(metrics)>12 or any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in metrics.values()):
            return None
        return event
    except (ValueError, TypeError):
        return None


class Device:
    def __init__(self, port, assistant):
        self.port = port
        self.assistant = assistant
        self.stop_event = threading.Event()
        self.thread = None
        self.connected = False
        self.last_error = ''
        self.stream = None
        self.record_lock = threading.Lock()
        self.pending = None

    def status(self):
        return {'available': bool(self.port and Path(self.port).exists()),
                'connected': self.connected, 'error': self.last_error,
                'recording': bool(self.pending)}

    def cancel_recording(self):
        with self.record_lock:
            pending = self.pending
            if pending:
                pending['error'] = 'Gravação cancelada.'
                pending['done'].set()
                try:
                    if self.stream: self.stream.write(('FERRIS_CANCEL ' + pending['id'] + '\n').encode())
                except (OSError, ValueError):
                    pass

    def record(self):
        hardware = self.assistant.hardware_state()
        with self.record_lock:
            if self.pending:
                raise UserError('Já existe uma gravação no ESP32.')
            if not self.connected or not self.stream:
                raise UserError('Conecte o ESP32 por USB para gravar.')
            if hardware['muted']:
                raise UserError('Desmute o ESP32 antes de gravar.')
            pending = {'id': uuid.uuid4().hex, 'pcm': bytearray(), 'sequence': 0,
                       'done': threading.Event(), 'error': '', 'begun': False}
            self.pending = pending
            stream = self.stream
        try:
            stream.write(('FERRIS_RECORD ' + pending['id'] + '\n').encode())
            if not pending['done'].wait(25):
                self.cancel_recording()
                raise UserError('O ESP32 não concluiu a gravação. Confira o firmware e o USB.')
            if pending['error']: raise UserError(pending['error'])
            after = self.assistant.hardware_state()
            if after['muted'] or after['revision'] != hardware['revision']:
                raise UserError('Gravação descartada: o estado do mute mudou.')
            output = io.BytesIO()
            with wave.open(output, 'wb') as wav:
                wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                wav.writeframes(pending['pcm'])
            return output.getvalue()
        except (OSError, ValueError) as exc:
            raise UserError('Falha na transferência USB.') from exc
        finally:
            with self.record_lock:
                if self.pending is pending: self.pending = None

    def _audio(self, line):
        with self.record_lock:
            pending = self.pending
            if not pending or pending['done'].is_set(): return
            fields = line.strip().split()
            if len(fields) < 2 or fields[1] != pending['id'].encode(): return
            try:
                if fields[0] == b'FERRIS_RECORD_BEGIN' and len(fields) == 2:
                    pending['begun'] = True
                elif fields[0] == b'FERRIS_AUDIO_ERROR':
                    raise ValueError('device rejected capture')
                elif fields[0] == b'FERRIS_AUDIO' and len(fields) == 4:
                    pcm = base64.b64decode(fields[3], validate=True)
                    if not pending['begun'] or int(fields[2]) != pending['sequence'] or len(pcm) != 800 or len(pending['pcm']) >= 64000:
                        raise ValueError('invalid frame')
                    pending['pcm'].extend(pcm); pending['sequence'] += 1
                elif fields[0] == b'FERRIS_AUDIO_END' and len(fields) == 4:
                    if len(pending['pcm']) != 64000 or int(fields[2]) != 64000 or binascii.crc32(pending['pcm']) != int(fields[3], 16):
                        raise ValueError('incomplete capture')
                    pending['done'].set()
            except (ValueError, binascii.Error):
                pending['error'] = 'Gravação interrompida ou inválida. Confira o mute e tente novamente.'
                pending['done'].set()

    def start(self):
        if not self.port or self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def stop(self):
        self.cancel_recording()
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
        self.connected = False

    def _read(self):
        while not self.stop_event.is_set():
            try:
                import serial
                stream = serial.Serial(port=None, baudrate=115200, timeout=.5, write_timeout=1, exclusive=True)
                stream.dtr = False; stream.rts = False; stream.port = self.port
                with stream:
                    self.stream = stream
                    self.connected = True; self.last_error = ''
                    while not self.stop_event.is_set():
                        line = stream.read_until(b'\n', size=2049)
                        self._audio(line)
                        if line.startswith(b'FERRIS_STATE ') and len(line)<=2048:
                            try:
                                state = self.assistant.update_hardware(json.loads(line[13:]))
                                if state['muted']: self.cancel_recording()
                            except (UserError,ValueError,TypeError,AttributeError):
                                pass
                        event = parse_event(line)
                        if event:
                            try:
                                self.assistant.wake(event['device'], event['confidence'], event.get('metrics'), event['event_id'])
                            except UserError:
                                pass
            except ImportError:
                self.last_error = 'Instale o extra device para receber eventos USB.'
                break
            except (OSError, ValueError):
                self.last_error = 'USB indisponível. Confira o cabo e feche outros monitores seriais.'
            self.cancel_recording()
            self.stream = None
            self.connected = False
            self.stop_event.wait(2)
