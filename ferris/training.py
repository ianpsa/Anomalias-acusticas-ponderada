"""Fixed local training job, initiated by the dashboard, never by the LLM."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

from .core import UserError
from .detector import ROOT


class Training:
    def __init__(self, data, detector, header=None, device=None):
        self.data = Path(data).resolve()
        self.detector = detector
        self.header = Path(header or ROOT/'firmware/main/model_weights.h')
        self.lock = threading.Lock()
        self.job = {'state': 'idle', 'message': 'Pronto para treinar.'}
        self.worker = None
        self.device = device
        self.flash_supported = os.getenv('FERRIS_FLASH_ENABLED', '1') == '1'

    def status(self):
        with self.lock:
            return {**self.job, 'model': self.detector.summary(), 'flash_supported': self.flash_supported}

    def start(self, mode, flash=False):
        if mode not in ('sessions', 'recordings'):
            raise UserError('Escolha avaliação por sessões ou treino experimental.')
        if type(flash) is not bool:
            raise UserError('Opção de gravação do ESP32 inválida.')
        if flash and not self.flash_supported:
            raise UserError('No Docker, treine pelo painel e use o serviço firmware do Compose para gravar a placa.')
        if flash and (not self.device or not self.device.status()['available']):
            raise UserError('Conecte o ESP32 por USB antes de gravar o firmware.')
        files = list((self.data/'recordings').glob('*/*/*.wav'))
        positives = sum(p.parts[-3] == 'ferris' for p in files)
        negatives = sum(p.parts[-3] in ('other','noise') for p in files)
        if positives < 12 or negatives < 12:
            raise UserError('Grave pelo menos 12 exemplos Ferris e 12 negativos antes de treinar.')
        if mode == 'sessions' and len({p.parent.name for p in files}) < 4:
            raise UserError('A avaliação por sessões precisa de pelo menos 4 sessões. Use experimental para uma primeira versão.')
        with self.lock:
            if self.job['state'] == 'running':
                raise UserError('Já há um treino em andamento.')
            job_id = uuid.uuid4().hex
            self.job = {'id': job_id, 'state': 'running', 'split_mode': mode,
                        'message': 'Treinando e validando o detector neste PC…'}
            self.worker = threading.Thread(target=self._run, args=(job_id, mode, flash), daemon=True)
            self.worker.start()
        return self.status()

    def _run(self, job_id, mode, flash):
        output = self.detector.root/'runs'/job_id
        log = self.data/'training'/f'{job_id}.log'
        activated = False
        try:
            output.mkdir(parents=True)
            log.parent.mkdir(parents=True, exist_ok=True)
            command = [sys.executable, str(ROOT/'tools/training/train_wake.py'),
                       '--data', str(self.data/'recordings'), '--output', str(output),
                       '--header', str(output/'model_weights.h'), '--split-mode', mode]
            with log.open('wb') as stream:
                result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                        timeout=300, env={**os.environ, 'OMP_NUM_THREADS':'1',
                                                          'OPENBLAS_NUM_THREADS':'1'})
            if result.returncode:
                # Show bounded diagnostics; never return the command or credentials.
                with log.open('rb') as stream:
                    stream.seek(max(0, log.stat().st_size-1500))
                    lines = stream.read().decode(errors='replace').strip().splitlines()
                raise UserError(lines[-1] if lines else 'O treinamento falhou.')
            self.detector.activate(output, self.header)
            activated = True
            state, message = 'ready', 'Modelo treinado e ativo no PC. Pesos do ESP32 exportados; regrave o firmware para atualizar a placa.'
            if flash:
                with self.lock:
                    self.job['message'] = 'Modelo treinado. Compilando e gravando o detector no ESP32 por USB…'
                self.device.stop()
                try:
                    with log.open('ab') as stream:
                        subprocess.run([sys.executable, str(ROOT/'tools/firmware/flash_esp32.py'), '--port', self.device.port],
                                       cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=600)
                    message = 'Modelo treinado e gravado no ESP32. Os serviços de voz continuam no PC de destino.'
                finally:
                    self.device.start()
        except subprocess.TimeoutExpired:
            state, message = 'error', ('A gravação do ESP32 excedeu o tempo limite. Modelo novo ativo no PC; tente gravar a placa novamente.'
                                       if activated else 'O treino excedeu 5 minutos. O modelo anterior foi mantido.')
        except Exception as exc:
            state = 'error'
            message = (str(exc) if isinstance(exc, UserError) else 'Não foi possível concluir o treino. Confira o log local.')
            message += (' Modelo novo ativo no PC; a atualização do ESP32 não foi concluída.' if activated else ' O modelo anterior foi mantido.')
        with self.lock:
            self.job.update(state=state, message=message)
