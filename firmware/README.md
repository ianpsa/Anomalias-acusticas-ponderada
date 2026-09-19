# Firmware Ferris

Alvo inicial: ESP32 original, ESP-IDF **5.4.2**, INMP441 em canal esquerdo e LED. Não conecte um alto-falante diretamente a um GPIO. A saída de voz desta fase é o PC; amplificador/DAC I2S e Bluetooth no ESP são extensões futuras.

## Ligações padrão

| INMP441 | ESP32 |
|---|---|
| VDD | 3,3 V |
| GND | GND |
| SCK | GPIO 26 |
| WS | GPIO 25 |
| SD | GPIO 33 |
| L/R | GND (canal esquerdo) |

LED: GPIO 2 com resistor adequado, ou LED embarcado se a placa o usar nesse pino. Confira o esquema da placa antes de conectar. Os pinos são configuráveis em `menuconfig`; as escolhas acima não se aplicam automaticamente a ESP32-S3/C3.

## Compilar

Com ESP-IDF 5.4.2 já instalado e seu ambiente ativado:

```bash
cd firmware
idf.py set-target esp32
idf.py menuconfig
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

Em **Ferris**, configure Wi-Fi, pinos, URL do evento no PC e token do dispositivo. O endereço deve terminar em `/api/device/wake`. As credenciais de Wi-Fi ficam em `sdkconfig`, ignorado pelo Git, e dentro do firmware compilado; não publique esses binários com credenciais pessoais.

Sem `main/model_weights.h`, o firmware compila em modo de diagnóstico: captura áudio e registra métricas, mas **não declara detecção de Ferris**. O treinamento executado na raiz gera esse cabeçalho junto com `models/wake.onnx`. Recompile e grave novamente após treinar.

## Concorrência

| Tarefa | Prioridade | Trabalho e sincronização |
|---|---:|---|
| `capture` | 5 | I2S com DMA, blocos de 800 amostras / 50 ms; entrega cópia ao ring buffer sem espera |
| `features` | 3 | Retira bloco emprestado do ring, copia para janela circular de 1 s, devolve imediatamente; extrai a cada 250 ms; fila de 3 vetores |
| `detect` | 2 | Consome vetor por cópia, executa os pesos equivalentes ao ONNX, exige duas janelas positivas, acende LED 300 ms, cooldown 3 s |
| `network` | 1 | Consome fila de 4 eventos, faz HTTP com timeout de 2 s; nunca bloqueia captura ou inferência |

O ring buffer `RINGBUF_TYPE_NOSPLIT` é finito (alocação de oito estruturas de bloco; overhead interno reduz a capacidade útil). Se cheio, a captura descarta o bloco novo e contabiliza a perda. Números de sequência identificam lacunas; a extração reinicia a janela, e a detecção reinicia as confirmações para não combinar trechos descontínuos. As filas têm envio sem espera, com contadores de descarte.

Não há ponteiros compartilhados para features ou eventos: as filas copiam os valores. Só a tarefa de features acessa a janela circular. Os contadores são acessados sob uma seção crítica curta `portMUX_TYPE`; nenhum mutex é mantido durante DSP, I2S, logs ou HTTP. A sincronização das filas e do ring buffer usa os mecanismos internos do FreeRTOS. Um Event Group sinaliza disponibilidade de Wi-Fi.

## Medições

A serial emite uma linha JSON dentro da mensagem de log, aproximadamente a cada segundo:

- `capture_us`: duração da leitura bloqueante do bloco I2S, incluindo espera por áudio. Não é tempo de CPU puro.
- `features_us`: cópia da janela ordenada e extração do vetor.
- `inference_us`: produto escalar e sigmoide do classificador.
- `decision_us`: tempo desde a conclusão da captura do último bloco da janela até a decisão, incluindo esperas nas filas. **Não inclui o segundo necessário para formar a janela acústica.**
- `drop_audio`, `drop_features`, `drop_network`: contadores de perda.

Meça também memória livre, watermark de stack e taxa de amostragem real na placa antes de finalizar o relatório. Tempo de espera da janela, segunda confirmação, rede, transcrição, LLM e síntese devem ser apresentados separadamente. O modelo linear pode ser rápido e ainda produzir uma latência perceptível devido à janela e às confirmações.

## Limite desta entrega inicial

O evento de ativação chega ao painel do PC, que reproduz a saudação. Captura da pergunta e resposta falada continuam no PC. O firmware não faz streaming da pergunta nem recebe PCM/TTS, e não implementa saída I2S ou Bluetooth de alto-falante. Esses caminhos dependem da definição da placa e da saída de áudio.
