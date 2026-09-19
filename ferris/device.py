"""USB wake events from ESP32; audio and language models remain on the PC."""
import json
import math
from pathlib import Path
import re
import threading


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

    def status(self):
        return {'available': bool(self.port and Path(self.port).exists()),
                'connected': self.connected, 'error': self.last_error}

    def start(self):
        if not self.port or self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
        self.connected = False

    def _read(self):
        while not self.stop_event.is_set():
            try:
                import serial
                stream = serial.Serial(port=None, baudrate=115200, timeout=.5, exclusive=True)
                stream.dtr = False; stream.rts = False; stream.port = self.port
                with stream:
                    self.connected = True; self.last_error = ''
                    while not self.stop_event.is_set():
                        line = stream.read_until(b'\n', size=2049)
                        event = parse_event(line)
                        if event:
                            self.assistant.wake(event['device'], event['confidence'], event.get('metrics'), event['event_id'])
            except ImportError:
                self.last_error = 'Instale o extra device para receber eventos USB.'
                break
            except (OSError, ValueError):
                self.last_error = 'USB indisponível. Confira o cabo e feche outros monitores seriais.'
            self.connected = False
            self.stop_event.wait(2)
