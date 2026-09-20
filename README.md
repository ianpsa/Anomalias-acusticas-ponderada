# Ferris

Detector de padrões acústicos embarcado. Um ESP32 com microfone INMP441 escuta continuamente e reconhece a palavra de ativação **Ferris** usando FreeRTOS e um modelo treinado. Ao detectar a palavra, a placa avisa o PC, que transcreve a pergunta seguinte, gera a resposta com um LLM local e fala em português.

Este é o projeto da ponderada de RTOS. O relatório técnico da entrega está em [RELATORIO.md](RELATORIO.md) e o enunciado em [assignment.pdf](assignment.pdf).

## Como o trabalho é dividido

| Onde | Responsabilidade |
|---|---|
| ESP32 | Captura I2S contínua, extração de features, inferência do detector, LEDs, botão de mute, envio de eventos por USB e Wi-Fi |
| PC conectado ao ESP32 | Painel web, captura/reprodução de áudio, ponte USB/Wi-Fi e treinamento do detector |
| PC de destino (`192.168.15.17`) | Whisper e Qwen no worker de voz (`8770`), Gemma no LM Studio (`1234`) |

A placa nunca executa Whisper, LLM ou síntese. O PC nunca participa da detecção embarcada. O artefato ONNX e os pesos exportados em C compartilham o mesmo extrator de features, então treino e firmware enxergam os mesmos números.

## Instalação

Requer Python 3.11 a 3.13 e um compilador C (`cc`/GCC) para a extração compartilhada.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[train,device]'
```

No PC de destino, instale as dependências e modelos de voz conforme **Voz em outra máquina** abaixo. O PC conectado ao ESP32 não precisa carregar Whisper nem Qwen quando o worker remoto está configurado.

### LM Studio

A conversa usa **Gemma** servido pelo LM Studio, que é instalado à parte. Os pesos ficam no diretório do LM Studio, fora deste repositório.

```bash
export PATH="$HOME/.lmstudio/bin:$PATH"
lms daemon up
lms get google/gemma-4-e2b@q4_k_m --gguf -y
lms load google/gemma-4-e2b --identifier ferris-gemma --context-length 4096 --parallel 1 --gpu off
lms server start --port 1234 --bind 127.0.0.1
```

Para usar um LM Studio que roda em outra máquina da rede, inicie o servidor lá com `--bind 0.0.0.0` e aponte a URL de conexão do painel para `http://IP_DA_OUTRA_MAQUINA:1234/v1`.

O cliente desliga o modo de raciocínio nas chamadas ao Gemma 4 para que o orçamento de geração vá para a resposta falada. Verifique o serviço com `lms ps` e `curl http://127.0.0.1:1234/v1/models`. Para liberar memória, `lms unload ferris-gemma`.

### Rodar

```bash
source .venv/bin/activate
python -m ferris.server
```

Abra **http://127.0.0.1:8765**. Em **Conexão**, informe a URL do LM Studio, clique em **Buscar modelos**, escolha o modelo e salve. As configurações ficam em `data/settings.json` com permissão `0600` e prevalecem sobre variáveis de ambiente.

O microfone do navegador exige contexto seguro. Use `localhost` no próprio PC. Abrir o painel de outro computador por HTTP simples não habilita o microfone remoto.

## Modos do painel

| Modo | O que faz |
|---|---|
| **ESP32 + Whisper remoto** | Modo principal. A placa detecta Ferris, o microfone do PC capta a pergunta, Whisper transcreve no destino e o LLM responde |
| **Testar detector neste PC + Whisper** | Mesma sequência usando só o microfone do PC, sem depender da placa |
| **Voz do navegador** | Reconhece Ferris por transcrição do navegador. Requer Chromium ou Chrome e pode processar áudio online. Não é o detector ONNX embarcado |
| **Chamar Ferris** | Testa a saudação contextual sem microfone nem modelo treinado |
| **Minha voz** | Grava e importa exemplos para o treinamento |

Cada ativação permite **uma** pergunta. Depois de responder, falhar na transcrição ou expirar a janela de 20 segundos, é preciso chamar Ferris de novo. Durante a fala do Ferris o reconhecimento fica suspenso para evitar autoativação. A versão é half duplex: use **Parar** para interromper uma resposta.

A captura da pergunta termina com cerca de 850 ms de silêncio ou no limite de 12 segundos. A detecção na placa exige duas janelas positivas consecutivas.

### Vozes

A opção padrão **Ferris, expressivo** usa Qwen3-TTS VoiceDesign em ONNX INT4, com descrição de voz masculina jovem e acolhedora em português brasileiro, sem alteração artificial de pitch. É lenta em CPU: no i5-1335U deste projeto, 4,56 s de áudio levaram **50,8 s** para gerar. Até 32 falas ficam em cache em `data/tts/qwen-design/cache`, e cada solicitação tem limite de 3 minutos. As opções **Rápida, voz 1 a 5** usam Supertonic 3 e respondem muito mais rápido. A escolha fica salva no navegador.

### Capacidades e limites

O serviço oferece ao modelo apenas `web_search`. Com uma chave **SerpApi** em Conexão, a consulta vai ao provedor e os resultados entram como dados para o modelo local, com as fontes no painel. Sem chave, o pedido devolve um link de busca em vez de inventar resultados.

Ferris não tem ferramentas de terminal, edição de arquivos ou execução de código, e o cliente orienta o modelo a recusar programação. O filtro de texto não garante que um LLM jamais produza código; a ausência de ferramentas executáveis é o limite efetivo. As conversas ficam apenas em memória, separadas por aba. Áudio não é enviado ao LM Studio, somente texto.

## Voz em outra máquina

A configuração desta instalação é:

| Serviço | Endereço | Modelo |
|---|---|---|
| LM Studio | `http://192.168.15.17:1234/v1` | `google/gemma-4-12b-qat` (confira em **Buscar modelos**) |
| Worker Whisper + voz | `http://192.168.15.17:8770/v1` | Whisper small + Qwen3-TTS VoiceDesign ONNX |
| Painel no PC do ESP32 | `http://127.0.0.1:8765` | Detector ONNX para treino/testes |

Em **Conexão**, salve os dois endereços, selecione o modelo de conversa e clique em
**Verificar Whisper e voz**. A mudança vale imediatamente e persiste em
`data/settings.json`, inclusive após reiniciar. O painel distingue transcrição local,
remota e serviço indisponível. Se o worker cair, não há fallback silencioso para
Whisper/Qwen neste PC. A reprodução da resposta continua no navegador deste PC.
A pergunta também é captada pelo microfone do navegador; a placa detecta a ativação,
e **Minha voz → ESP32** grava exemplos de treino pelo microfone da placa.

No **PC de destino**, dentro deste repositório e com o ambiente Python ativado:

```bash
python -m pip install -e '.[train,voice,tts]'
python tools/setup_whisper.py
python tools/setup_designed_tts.py
python tools/voice_worker.py --host 0.0.0.0 --port 8770
```

Os modelos ficam em `data/whisper` e `data/tts`, fora do Git. O download é feito uma
vez; `setup_designed_tts.py` baixa cerca de 1,75 GB com revisões e SHA-256 fixados.
Ao atualizar o código do worker, reinicie esse processo no destino. Ele pode aquecer
Qwen em segundo plano e rejeita pedidos de síntese simultâneos, mantendo o cancelamento
restrito ao `request_id` da fala. O cliente também funciona com a versão anterior do
worker; para proteger pedidos de vários clientes, atualize o worker.

O worker oferece `/v1/audio/transcriptions` (WAV bruto ou multipart com campo `file`),
`/v1/audio/speech` (JSON `input`, `voice: "ferris"`, `response_format: "wav"`) e
`/health`. São as rotas usadas pelo Ferris, não uma implementação completa da API
OpenAI. O endereço do LM Studio é usado separadamente para modelos e conversa.

Para configurar por terminal no PC do ESP32, antes do primeiro uso:

```bash
export LM_STUDIO_URL=http://192.168.15.17:1234/v1
export LM_STUDIO_MODEL=google/gemma-4-12b-qat
export FERRIS_VOICE_URL=http://192.168.15.17:8770/v1
python -m ferris.server
```

Configurações já salvas têm prioridade sobre essas variáveis. Também existe
`--voice-url http://192.168.15.17:8770/v1` para sobrescrever o destino na inicialização.
A `.env.example` documenta as variáveis, mas não é carregada automaticamente.
Defina `FERRIS_VOICE_TOKEN` no worker e salve o mesmo token no campo de voz do painel
para exigir autenticação; esse token é separado da chave do LM Studio. Sem token,
o worker aceita clientes da rede.

Confira os serviços a partir do PC do ESP32 (inclua `Authorization: Bearer …` se
configurou autenticação):

```bash
curl --connect-timeout 5 http://192.168.15.17:1234/v1/models
curl --connect-timeout 5 http://192.168.15.17:8770/health
```

Se houver timeout, confira se o PC está acordado, na mesma rede e se o firewall permite
1234 e 8770. O LM Studio precisa aceitar conexões de rede, e o worker precisa escutar
em `0.0.0.0`. Para executar voz localmente de novo, deixe o endereço do worker vazio e
instale `.[voice,tts]` e seus modelos neste PC; `setup_tts.py` habilita as vozes rápidas.

### Desempenho da síntese

A geração é autoregressiva e roda em CPU: por frame de áudio são um passo do talker,
quinze chamadas ao preditor de resíduo e um embed. O custo cresce com o tamanho da
resposta, então respostas curtas são o maior fator. O prompt já pede até 3 frases.

Trechos de texto diferentes são independentes, então são gerados em paralelo, o que
rende cerca de 1,5x em respostas longas sem mudar uma amostra do áudio. O número de
threads por sessão ONNX sai de `QWEN_INTRA_THREADS`, padrão 4. Medido em um M3 Pro,
com três trechos de 135 caracteres: 84,4 s em série contra 56,6 s em paralelo.

`FERRIS_TTS_SENTENCE_CHUNKS=1` corta o texto por frase em vez de por comprimento, o que
cria mais trechos para paralelizar. Rende mais em respostas de várias frases curtas, mas
insere 200 ms de pausa entre frases e muda a prosódia, então avalie de ouvido antes de
adotar.

## Montagem do hardware

Alvo: ESP32 original (placa ESP-32U), ESP-IDF **5.4.2**, INMP441 no canal esquerdo.

| INMP441 | ESP32 |
|---|---|
| VDD | 3,3 V |
| GND | GND |
| SCK | GPIO 26 |
| WS | GPIO 25 |
| SD | GPIO 22 |
| L/R | GND (canal esquerdo) |

| Controle | Ligação |
|---|---|
| LED vermelho, captura inativa ou mute | GPIO 18, resistor 330 Ω, ânodo; cátodo em GND |
| LED verde, escuta ativa | GPIO 19, resistor 330 Ω, ânodo; cátodo em GND |
| Botão de mute | GPIO 23, botão normalmente aberto, GND; pull-up interno |

SCK e WS são sinais distintos e não podem compartilhar um GPIO. Cada LED precisa do próprio resistor. Num botão de quatro pernas, use dois contatos que só tenham continuidade quando pressionado. Um capacitor cerâmico de 100 nF (`104`) próximo ao microfone desacopla VDD e GND. Desconecte o USB durante a montagem. Os pinos são configuráveis em `menuconfig` e estas escolhas não valem automaticamente para ESP32-S3 ou C3.

**Não conecte um alto-falante direto a um GPIO.** A saída de voz desta fase é o PC. Amplificador ou DAC I2S e Bluetooth na placa são extensões futuras; o ESP32-S3 não suporta Bluetooth Classic nem A2DP.

### Comportamento dos LEDs e do mute

O firmware inicia em escuta: vermelho durante a preparação, depois verde. Um clique silencia, outro retoma, com debounce de 30 ms amostrado a cada 10 ms. Se o botão estiver pressionado no boot, precisa ser solto antes do primeiro clique. O mute não persiste após reiniciar.

Ao silenciar, a captura para de publicar blocos e desativa I2S após a leitura corrente (timeout de 200 ms). Áudio, features e eventos antigos são invalidados por uma geração que muda a cada clique. Na retomada, os quatro primeiros blocos são descartados para renovar o DMA e uma janela nova de 1 segundo precisa ser preenchida. Eventos já enviados pela rede não podem ser desfeitos. VDD continua em 3,3 V: o mute corta a captura, não a alimentação.

O verde apaga por 300 ms ao detectar Ferris e volta a acender. O vermelho indica mute, preparação ou captura indisponível. Verde significa processamento habilitado, não gravação persistente nem garantia de modelo treinado.

## Compilar e gravar o firmware

Com o ESP-IDF 5.4.2 ativado:

```bash
cd firmware
idf.py set-target esp32
idf.py menuconfig
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

Em **Ferris**, no `menuconfig`, configure Wi-Fi, pinos, URL do evento no PC e token do dispositivo. O endereço deve terminar em `/api/device/wake`. As credenciais de Wi-Fi ficam em `sdkconfig`, ignorado pelo Git, e dentro do binário compilado: não publique esses binários com credenciais pessoais.

Se já existir um `sdkconfig` antigo, confirme SD 22, LEDs 18 e 19 e botão 23 no `menuconfig`. Mudar o padrão no código não substitui uma configuração salva. A inicialização rejeita GPIOs duplicados.

Sem `main/model_weights.h`, o firmware compila em **modo de diagnóstico**: captura áudio e registra métricas, mas não declara detecção. O treinamento gera esse cabeçalho; recompile e grave de novo depois de treinar.

### Conexão com o PC

O servidor identifica automaticamente a ponte CP2102 no Linux. Feche outros monitores seriais antes de iniciar o Ferris. Para escolher a porta, `python -m ferris.server --serial-port /dev/ttyUSB0`. Para usar somente Wi-Fi, `--serial-port ''`.

Para receber eventos pela rede:

```bash
export FERRIS_TOKEN='defina-um-token-de-painel-com-24-ou-mais-caracteres'
export FERRIS_DEVICE_TOKEN='defina-outro-token-para-o-esp32'
python3 -m ferris.server --host 0.0.0.0
```

Informe `FERRIS_TOKEN` em **Conexão** e o mesmo `FERRIS_DEVICE_TOKEN` no firmware. O token do dispositivo autoriza apenas eventos de ativação, não dá acesso às configurações. O transporte HTTP inicial não é cifrado, então use uma rede confiável.

USB e Wi-Fi usam o mesmo `event_id` (identificador de boot mais contador) e a ponte descarta duplicatas, evitando duas saudações quando os dois caminhos chegam ao mesmo servidor.

## Treinar o detector ONNX

O detector usa RMS, centroide espectral e 13 MFCCs em dez intervalos temporais, totalizando **150 features por janela de 1 segundo**. O modelo é uma regressão logística regularizada, compacta o suficiente para exportar a inferência ao ESP32.

### Coletar exemplos

Em **Minha voz**, colete as classes `Ferris`, `Outras palavras` e `Ambiente`. A entrada padrão é **ESP32, microfone conectado à placa (USB)**, para que o treino receba áudio do mesmo microfone da detecção. Com o LED verde aceso, escolha a classe e clique em **Gravar exemplo de 2 segundos**; diga a palavra assim que clicar e aguarde a transferência, cerca de 10 segundos a 115200 baud. Os arquivos novos recebem prefixo `esp32-`.

Diga a palavra uma vez no centro do clipe. Varie distância, intensidade e ambiente. Inclua negativos úteis: palavras parecidas como "férias" e "feliz", conversa normal, TV, silêncio e ruído. A opção **Microfone deste PC** continua disponível, com medidor de nível ao vivo, que não aparece na coleta por USB.

Mude o campo **Sessão de gravação** a cada dia ou ambiente, usando nomes como `esp-sala-01`. O pipeline exige no mínimo quatro sessões diferentes, com positivos e negativos em cada uma, e pelo menos 12 positivos e 12 negativos no total. Esse mínimo só serve para executar o pipeline: comece com dezenas ou centenas de exemplos variados para avaliar utilidade real.

Arquivos com todas as amostras zeradas são rejeitados; confira o mute do sistema e a entrada selecionada. Silêncio real pode ter sinal muito baixo e continua válido como negativo.

### Treinar

No painel, **Minha voz, Treinar o detector**. Escolha **Experimental** para uma primeira coleta ou **Sessões diferentes** para avaliação entre sessões, e clique em **Treinar e usar modelo**. O trabalho roda em segundo plano e a página pode ser recarregada. Marque **Gravar também no ESP32 conectado por USB** para compilar e gravar os pesos na placa no mesmo fluxo, o que exige Docker acessível, a imagem `espressif/idf:v5.4.2` e acesso à porta serial.

Pelo terminal:

```bash
python tools/train_wake.py                      # exige sessões diferentes
python tools/train_wake.py --split-mode recordings   # versão experimental
```

O pipeline valida WAV mono PCM 16 bits a 16 kHz e rejeita duplicatas; separa **sessões inteiras** em treino, validação e teste antes de extrair janelas; treina com aumento por deslocamento temporal apenas no treino; escolhe o limiar na validação; avalia no teste reservado; gera `models/wake.onnx`, `models/wake.json` e `firmware/main/model_weights.h`; e compara a probabilidade ONNX com a implementação C exigindo erro absoluto de no máximo `1e-4`.

`--split-mode recordings` separa arquivos inteiros em cerca de 60/20/20 mantendo as classes nos três conjuntos. A sessão pode se repetir entre conjuntos, portanto **as métricas não medem generalização** para outro dia, ambiente ou microfone. O relatório registra `split_mode`, hashes por conjunto e essa limitação. Não renomeie gravações da mesma coleta como sessões diferentes.

### Artefatos

`wake.onnx` recebe `features: float32[batch, 150]` e retorna `probability: float32[batch, 1]`. O PC executa com ONNX Runtime; o ESP32 executa os mesmos pesos como produto escalar mais sigmoide, sem ONNX Runtime. Ambos usam o mesmo extrator C. **O arquivo ONNX é o artefato de modelo da entrega.**

`wake.json` guarda limiar, pesos, métricas, grupos de treino, validação e teste, e hashes de modelo, DSP e dados.

O treino pelo painel cria versões em `models/runs/<id>/`, valida a execução e troca `models/active.json` sem reiniciar o serviço. Uma falha de treino mantém o detector anterior. Os logs ficam em `data/training/`. Esses arquivos são ignorados pelo Git: depois de avaliar um modelo real, selecione explicitamente os artefatos para a entrega e revise os metadados locais de `wake.json` antes de publicar, sem incluir seus áudios privados.

Não há modelo pré-treinado neste repositório; ele depende de gravações reais. Não use os modelos sintéticos dos testes como detector de fala. Um detector treinado só com a sua voz não garante reconhecer outras pessoas e não serve como autenticação de identidade.

Para importar gravações em outro formato, converta e recorte clipes de até 5 segundos:

```bash
ffmpeg -i exemplo.m4a -ac 1 -ar 16000 -c:a pcm_s16le exemplo.wav
```

## Validação

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
PYTHON_BIN=python node tools/browser_smoke.mjs
```

O smoke de navegador precisa de Chromium e Node.js. Usa microfone artificial e servidor HTTP simulado, sem conectar a um modelo real nem capturar sua voz. Os testes automatizados usam tons sintéticos para verificar fluxo, separação de dados e equivalência ONNX/C; **não medem acurácia da palavra Ferris**.

Para avaliar áudio contínuo com o modelo real:

```bash
python tools/benchmark.py teste-continuo.wav --events eventos.json
```

`eventos.json` lista os instantes em segundos em que cada palavra termina, por exemplo `[2.4, 8.1, 15.6]`; use `[]` para áudio só negativo. O script mede p50, p95 e p99 de features e inferência **no PC**, perdas, falsos acionamentos por hora e atraso até a ativação. Esses números não podem ser apresentados como latência do ESP32; para o dispositivo, use as métricas da serial descritas em [RELATORIO.md](RELATORIO.md).

## Organização

- `ferris/`: serviço local, cliente LM Studio, voz e painel.
- `firmware/`: projeto ESP-IDF e frontend DSP compartilhado.
- `tools/`: treinamento, benchmark, setup de modelos e smoke de navegador.
- `tests/`: testes de integração e do modelo.
- `models/`: artefatos do detector, ignorados pelo Git.
- `data/`: áudios, modelos de voz, logs e configurações, ignorados pelo Git.

## Referências

- [Gemma 4 E2B no LM Studio](https://lmstudio.ai/models/google/gemma-4-e2b), [serviço headless llmster](https://lmstudio.ai/docs/developer/core/headless) e [ferramentas na API compatível](https://lmstudio.ai/docs/developer/openai-compat/tools).
- [ESP-IDF 5.4.2: I2S](https://docs.espressif.com/projects/esp-idf/en/v5.4.2/esp32/api-reference/peripherals/i2s.html) e [suporte a áudio Bluetooth por chip](https://docs.espressif.com/projects/esp-adf/en/latest/solution-center/bluetooth-audio.html).
- [MDN: SpeechRecognition](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition) e [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
- [Qwen3-TTS VoiceDesign](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign) e o [export ONNX Community](https://huggingface.co/onnx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign). O adaptador em `ferris/vendor/qwen_onnx.py` deriva do exemplo Apache-2.0 desse export; as alterações e a licença estão no diretório.
- [Arquivo oficial Supertonic](https://github.com/supertone-oss-archive/supertonic). O projeto upstream foi arquivado; usamos o SDK `supertonic==1.3.1` com revisão fixa dos pesos e download automático desabilitado.
