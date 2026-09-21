"""Container liveness; the panel must remain usable when its remote PC is down."""
import json
import os
import sys
from urllib.request import Request, urlopen

voice = sys.argv[1] == 'voice'
url = 'http://127.0.0.1:8770/health' if voice else 'http://127.0.0.1:8765/'
token = os.getenv('FERRIS_VOICE_TOKEN' if voice else 'FERRIS_TOKEN', '')
req = Request(url, headers={'Authorization': 'Bearer ' + token} if token else {})
with urlopen(req, timeout=3) as response:
    if voice:
        status = json.load(response)
        if not (status.get('stt') and status.get('tts')):
            raise SystemExit(1)
