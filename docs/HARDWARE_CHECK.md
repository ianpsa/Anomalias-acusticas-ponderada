# Primeiro teste na placa — 19/09/2026

Firmware compilado com ESP-IDF 5.4.2, gravado por USB em `/dev/ttyUSB0` e iniciado no **ESP32-D0WD-V3, revisão 3.1**, identificado na placa ESP-32U do usuário. O esptool verificou os hashes após a gravação. Clock de CPU observado no boot: **160 MHz**. Monitor serial: **115200 baud**.

Pinagem confirmada no log: **SD 22, SCK 26, WS 25, vermelho 18, verde 19, botão 23**. O botão usa pull-up interno e alternância ativa em nível baixo.

O firmware iniciou, produziu vetores de features e registrou várias transições `muted: true/false` pelo botão físico. Os logs de processamento cessaram durante mute; depois de uma retomada suficientemente longa, voltaram a aparecer. O último estado observado foi **silenciado**. A cor/brilho dos LEDs não foi verificada visualmente pelo agente.

Três amostras de diagnóstico efetivamente observadas:

| Uptime (s) | RMS médio | Leitura I2S (ms) | Features (ms) | Último bloco → decisão (ms) |
|---:|---:|---:|---:|---:|
| 1,930 | 0,135222 | 47,962 | 100,782 | 100,887 |
| 2,937 | 0,075009 | 47,962 | 100,007 | 100,096 |
| 14,391 | 0,040061 | 47,962 | 100,014 | 100,108 |

Os contadores `drop_audio`, `drop_features` e `drop_network` eram zero nessas amostras. O sinal não era nulo e variou entre janelas; isso não constitui avaliação de qualidade de fala ou de acurácia. São poucas amostras de um teste funcional, insuficientes para estimar p95/p99 ou garantir deadlines sob carga. A leitura inclui espera por amostras, e a decisão não inclui o segundo da janela acústica.

**Modelo ausente:** `Ferris model ready: 0`. O classificador não foi executado com pesos treinados; `score=0` e `inference_us=0–1` são diagnósticos e não medem a inferência de um modelo real. Wi-Fi/ponte remota não foram configurados nesta gravação.

A placa tem flash física de 4 MiB; esta compilação usa o layout padrão com cabeçalho de 2 MiB e partição de aplicação de 1 MiB. O boot reportou essa diferença e iniciou usando o tamanho configurado; não houve erro de inicialização observado.

Os eventos estruturados ficaram em `data/hardware-smoke.json`, ignorado pelo Git. A compilação local está em `build/esp32-upload/`, também ignorada. O monitor foi encerrado e a porta liberada; o firmware permanece na flash e volta a iniciar ao ligar a placa.
