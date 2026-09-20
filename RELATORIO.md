# Relatório técnico: detector de padrões acústicos com FreeRTOS

Projeto da ponderada de RTOS descrita em [assignment.pdf](assignment.pdf). Instalação e operação estão no [README.md](README.md).

## 1. Objetivo e padrão escolhido

O enunciado permite escolher livremente o padrão acústico a detectar. O padrão escolhido é a **palavra de ativação "Ferris"**, usada para acionar um assistente de voz.

A aplicação prática é interação sem as mãos com a detecção acontecendo localmente no ESP32: o áudio contínuo nunca sai da placa, e só um evento de ativação é transmitido. Isso reduz tráfego e mantém a escuta permanente dentro do dispositivo.

Vale registrar uma diferença honesta em relação ao título do enunciado: palavra de ativação é **classificação supervisionada de um padrão conhecido**, não detecção estatística de anomalias em sentido estrito. A estrutura exigida é a mesma, com captura contínua, extração de features e um modelo pré-treinado aplicado em tempo real.

## 2. Arquitetura RTOS

O enunciado pede no mínimo três tarefas concorrentes sincronizadas. A implementação usa **seis**, incluindo as três exigidas.

![Tarefas e sincronização FreeRTOS](rtos.svg)

### Tarefas e prioridades

| Tarefa | Prior. | Trabalho e sincronização |
|---|---:|---|
| `capture` | 5 | I2S com DMA, blocos de 800 amostras (50 ms); entrega cópia ao ring buffer sem espera |
| `controls` | 4 | Botão com debounce, mute por geração, LEDs vermelho e verde; polling de 10 ms |
| `features` | 3 | Retira bloco emprestado do ring, copia para janela circular de 1 s, devolve imediatamente; extrai a cada 250 ms; fila de 3 vetores |
| `detect` | 2 | Consome vetor por cópia, executa os pesos equivalentes ao ONNX, exige duas janelas positivas, pulsa o LED verde por 300 ms, cooldown de 3 s |
| `recording` | 1 | Comandos USB, buffer de 64 kB para exemplos de 2 s, transferência em blocos com CRC32; inferência suspensa durante a coleta |
| `network` | 1 | Consome fila de 4 eventos, emite evento USB e faz HTTP com timeout de 2 s quando há Wi-Fi; nunca bloqueia captura ou inferência |

As três tarefas exigidas pelo enunciado são `capture` (alta prioridade, I2S para buffer circular), `features` (prioridade média, RMS, centroide espectral e MFCCs para fila compartilhada) e `detect` (prioridade baixa, modelo pré-treinado sobre as features, com alerta em LED).

### Sincronização e resolução de conflitos

**Ring buffer entre captura e features.** Um `RINGBUF_TYPE_NOSPLIT` com alocação de oito estruturas de bloco. A capacidade útil é menor que oito por causa do overhead interno. Se estiver cheio, a captura **descarta o bloco novo** e incrementa um contador, em vez de bloquear. Preservar a tarefa de maior prioridade é deliberado: atrasar o I2S causaria perda de amostras no DMA, que é irrecuperável, enquanto descartar um bloco inteiro é visível e contabilizado.

**Filas por cópia.** Não há ponteiro compartilhado para features nem para eventos: as filas copiam os valores. A janela circular de 1 s é acessada apenas pela tarefa `features`. Isso elimina a classe inteira de conflitos de ownership de buffer, ao custo de cópias pequenas e previsíveis.

**Detecção de lacunas.** Números de sequência identificam blocos perdidos. Quando há lacuna, a extração reinicia a janela e a detecção reinicia a contagem de confirmações, para nunca combinar trechos descontínuos em uma mesma decisão.

**Seções críticas.** Os contadores de diagnóstico são atualizados sob um `portMUX_TYPE` curto. **Nenhum mutex é mantido durante DSP, I2S, logs ou HTTP**, o que evita inversão de prioridade nos caminhos longos. A coleta USB compartilha um buffer estático de 64 kB com um mutex breve, usado apenas para cópia e codificação; a transmissão serial acontece fora dele.

**Mute por geração.** Um contador de geração muda a cada clique do botão. Áudio, features e eventos carimbados com geração antiga são descartados, o que resolve a corrida entre o clique e os dados já em trânsito nas filas. Um Event Group sinaliza disponibilidade de Wi-Fi.

**Envio sem espera.** Todas as filas usam envio sem bloqueio com contador de descarte, de modo que nenhuma tarefa lenta (rede, gravação) possa causar backpressure na captura.

## 3. Análise de latência

### Instrumentação

A serial emite uma linha JSON por segundo, aproximadamente, com:

| Campo | Significado |
|---|---|
| `rms_mean` | Média dos RMS dos dez intervalos, em amplitude normalizada. Não é dB SPL calibrado |
| `capture_us` | Duração da leitura bloqueante do bloco I2S, **incluindo a espera por áudio**. Não é tempo de CPU |
| `features_us` | Cópia da janela ordenada e extração do vetor de 150 features |
| `inference_us` | Produto escalar e sigmoide do classificador |
| `decision_us` | Da conclusão da captura do último bloco da janela até a decisão, incluindo esperas nas filas. **Não inclui o segundo necessário para formar a janela acústica** |
| `drop_audio`, `drop_features`, `drop_network` | Contadores de perda |

### Medições em hardware

Primeiro teste na placa, **19/09/2026**. Firmware compilado com ESP-IDF 5.4.2 e gravado por USB em `/dev/ttyUSB0`, no **ESP32-D0WD-V3 revisão 3.1** da placa ESP-32U. O esptool verificou os hashes após a gravação. Clock observado no boot: **160 MHz**. Monitor serial a **115200 baud**. Pinagem confirmada no log: SD 22, SCK 26, WS 25, vermelho 18, verde 19, botão 23, com pull-up interno e alternância ativa em nível baixo.

| Uptime (s) | RMS médio | Leitura I2S (ms) | Features (ms) | Último bloco até decisão (ms) |
|---:|---:|---:|---:|---:|
| 1,930 | 0,135222 | 47,962 | 100,782 | 100,887 |
| 2,937 | 0,075009 | 47,962 | 100,007 | 100,096 |
| 14,391 | 0,040061 | 47,962 | 100,014 | 100,108 |

`drop_audio`, `drop_features` e `drop_network` eram **zero** nas três amostras.

A leitura I2S estável em 47,96 ms corresponde ao bloco de 800 amostras a 16 kHz (50 ms nominais), confirmando que a tarefa de captura acompanha o relógio do microfone. O caminho do último bloco até a decisão ficou em torno de 100 ms, dominado pelo passo de extração.

### Latência percebida pelo usuário

A latência fim a fim é maior que os 100 ms acima e precisa ser apresentada em partes:

1. Formação da janela acústica de 1 s, não incluída em `decision_us`.
2. Segunda janela positiva exigida para confirmar, somando outro passo de 250 ms.
3. Cooldown de 3 s após uma ativação.
4. Transporte do evento, USB ou HTTP com timeout de 2 s.
5. No PC: transcrição com Whisper, geração com o LLM e síntese de voz.

O modelo linear é rápido e ainda assim a ativação é perceptível, porque o custo está na janela e nas confirmações, não na inferência.

## 4. Resultados

### Verificado em hardware

O firmware iniciou, produziu vetores de features e registrou várias transições `muted: true/false` pelo botão físico. Os logs de processamento cessaram durante o mute e voltaram após uma retomada suficientemente longa. O último estado observado foi silenciado. O sinal não era nulo e variou entre janelas.

A placa tem flash física de 4 MiB; esta compilação usa o layout padrão, com cabeçalho de 2 MiB e partição de aplicação de 1 MiB. O boot reportou a diferença e iniciou com o tamanho configurado, sem erro de inicialização.

### Verificado no PC

A suíte automatizada roda **38 testes** cobrindo fluxo do serviço, separação de dados do treino e equivalência numérica entre o ONNX e a implementação C, com erro máximo observado de **5,97e-08**, bem abaixo do limite de `1e-4` exigido pelo pipeline.

Em 19/09/2026 o Ferris foi conectado ao Gemma 4 E2B Q4_K_M pelo LM Studio local em um i5-1335U. Duas respostas consecutivas em português levaram **9,4 s e 8,1 s**, com continuidade de contexto. O pedido de programação foi recusado pelo cliente e a pesquisa sem chave retornou um link do Google.

### O que ainda não foi verificado

**O classificador não rodou com pesos treinados na placa.** O log registrou `Ferris model ready: 0`, portanto `score=0` e `inference_us` entre 0 e 1 µs são valores de diagnóstico e **não medem a inferência de um modelo real**. Wi-Fi e ponte remota não foram configurados nesta gravação. A cor e o brilho dos LEDs não foram verificados visualmente. Os números de latência do LLM são verificações pontuais, não um benchmark.

## 5. Discussão e limitações

**A amostragem é pequena.** Três amostras de um teste funcional não permitem estimar p95 ou p99, nem afirmar que os deadlines se mantêm sob carga. Uma avaliação adequada precisa de execução longa, com o detector treinado ativo e com a coleta USB concorrente, que é o cenário que mais disputa CPU e barramento.

**Falta medir folga de recursos.** Memória livre, watermark de stack por tarefa e taxa de amostragem real na placa ainda não foram registrados. São essas medidas que sustentariam a afirmação de que a arquitetura tem margem.

**As métricas de acurácia disponíveis são sintéticas.** Os testes automatizados usam tons gerados, não fala. O resultado perfeito do conjunto de teste sintético mede a corretude do pipeline, não a qualidade do detector. A acurácia real depende de gravações reais em várias sessões, com o próprio INMP441.

**O modo experimental não mede generalização.** Com `--split-mode recordings`, a mesma sessão pode aparecer em treino e teste, então as métricas não dizem nada sobre outro dia, ambiente ou microfone. Isso fica registrado em `split_mode` e `limitations` dentro de `wake.json`.

**Escopo do alerta.** O alerta de anomalia é o pulso no LED verde, conforme o enunciado permite (LED ou buzzer). Não há buzzer na montagem atual.

**A saída de voz não é embarcada.** Whisper, LLM e síntese executam no PC. A placa faz detecção, controles e transporte de eventos, e não faz streaming da pergunta nem recebe PCM.

## 6. Reprodução

O script de teste exigido pelo enunciado é `tools/benchmark.py`, que simula áudio contínuo com anomalias marcadas e mede desempenho:

```bash
python tools/benchmark.py teste-continuo.wav --events eventos.json
```

`eventos.json` lista os instantes em segundos em que cada palavra termina, como `[2.4, 8.1, 15.6]`; use `[]` para áudio exclusivamente negativo. O script reporta p50, p95 e p99 de features e inferência, perdas, falsos acionamentos por hora e atraso até a ativação. Os valores são medidos **no PC** e não substituem as métricas da serial do ESP32.

Os eventos estruturados do teste de hardware ficaram em `data/hardware-smoke.json` e a compilação em `build/esp32-upload/`, ambos ignorados pelo Git.

## 7. Estado do projeto

| Parte | Estado |
|---|---|
| Painel, conversa por texto e cliente LM Studio | Validados com Gemma 4 E2B local e com servidor simulado nos testes |
| Saudações por horário e nome | Implementadas, independentes do LM Studio |
| Pesquisa Google | Integração SerpApi implementada; depende de chave e do provedor |
| Gravação de exemplos e pipeline de treinamento | Implementados |
| Inferência ONNX no PC e export equivalente em C | Implementados, equivalência numérica testada |
| Modelo que reconhece "Ferris" | Treino disponível no painel; a qualidade depende das gravações reais |
| Firmware FreeRTOS | Compilado e gravado; captura e mute verificados em hardware. Inferência real depende do detector treinado |
| Whisper, LLM e reprodução da resposta | Executados no PC |

`main` é a branch principal. A implementação é desenvolvida em `feat/ferris-assistant`.
