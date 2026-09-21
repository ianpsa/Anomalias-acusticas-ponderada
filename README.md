## Ferris

&emsp; Minha ideia aqui foi construir uma pequena companhia que eu pudesse chamar pelo nome, falar alguma coisa e receber uma resposta em português, tipo uma Alexa, só que com um ESP32 na mesa e os modelos rodando nos meus próprios computadores. O ponto de partida é bem simples: alguém fala "Ferris", ele cumprimenta de acordo com o horário e espera uma pergunta. Depois de responder ele volta a esperar o nome, até porque ninguém merece um assistente entrando em toda conversa da sala hehe.

<br>

&emsp; Separei o trabalho entre a placa e os computadores porque cada um tem sua função nessa história. O ESP32 fica com o microfone, o detector e o botão de mute; o PC conectado nele fica com o painel e o treino; e o outro PC faz a parte pesada com Whisper, Gemma no LM Studio e a voz Qwen. Coloquei o que é nosso no Docker Compose para não precisar montar um ambiente Python na mão toda vez que quiser usar o Ferris. O LM Studio continua instalado no PC de destino, ele já resolve a parte de servir o Gemma.

<br>

### Como rodar

&emsp; Precisa do Docker com Compose. Na montagem atual o PC do ESP32 usa Linux, e o PC de destino está em `192.168.15.17`. O endereço da conversa é `1234/v1`, e o da voz é `8770/v1`, são dois serviços diferentes!

**Neste PC, com o ESP32 conectado por USB:**

```sh
cp .env.example .env  # só na primeira vez
# Se sua porta não for ttyUSB0, ajuste FERRIS_SERIAL_PORT no .env.
echo "FERRIS_SERIAL_GID=$(stat -c '%g' /dev/ttyUSB0)" >> .env
docker compose up -d --build ferris
```

Abra **http://localhost:8765**. Pronto, o painel está no ar! Em **Conexão**, confira:

| Campo | Valor nesta instalação |
| --- | --- |
| LM Studio | `http://192.168.15.17:1234/v1` |
| Modelo | `google/gemma-4-12b-qat` |
| Serviço de Whisper e voz | `http://192.168.15.17:8770/v1` |

Clique em **Buscar modelos** e **Verificar Whisper e voz**. Salvar aplica a conexão na hora e mantém a escolha depois de reiniciar. Se mudar o `.env` e continuar aparecendo o endereço antigo, é porque o que foi salvo no painel tem prioridade; altere por lá.

> obs: se a versão antiga estiver rodando pelo Python, encerre ela primeiro. A porta 8765 e o USB precisam ficar livres para o container.

<br>

**No PC de destino, dentro deste mesmo repositório:**

```sh
cp .env.example .env  # só se ainda não existir
docker compose up -d --build voice
```

&emsp; Na primeira vez ele baixa Whisper small e Qwen3-TTS ONNX e depois inicia o worker na porta **8770**. Os arquivos ficam em `data/`, então não precisa baixar de novo a cada subida. Se os modelos já estiverem nessa pasta, o container aproveita os mesmos arquivos. Essa primeira execução demora mais, acompanhe com `docker compose logs -f voice`.

&emsp; No LM Studio, mantenha o Gemma carregado e o servidor acessível pela rede na porta **1234**. Se já existir um worker de voz iniciado pelo Python na 8770, pare esse processo antes de subir o container. O Compose deste PC sobe só `voice`, não abre o painel nem tenta acessar o ESP32.

<br>

**Para testar o painel sem a placa:**

```sh
COMPOSE_FILE=compose.yaml docker compose up -d --build ferris
```

**Comandos do dia a dia:**

```sh
docker compose ps                 # vê o que está rodando
docker compose logs -f ferris      # logs do painel
docker compose logs -f voice       # no PC que está gerando a voz
docker compose up -d --build ferris # atualiza o painel depois de mudar o código
docker compose down               # para os containers; data/ e models/ continuam aqui
```

---

### Falando com ele

&emsp; Ative o microfone no painel, escolha **ESP32 + Whisper remoto** e diga "Ferris". A placa detecta o nome, ele fala a saudação e abre uma janela de 20 segundos para uma pergunta. A pergunta usa o microfone do navegador, vai para o Whisper no PC de destino e só o texto segue para o Gemma. A voz da resposta também é gerada no destino, mas toca no navegador deste PC. A placa ainda não transmite a pergunta nem reproduz a resposta.

&emsp; Depois disso, para perguntar de novo é só chamar "Ferris" outra vez. O botão **Parar** interrompe a resposta, e o botão físico bloqueia a escuta. Também dá para conversar por texto ou usar **Chamar Ferris** para testar a saudação sem depender do detector.

> obs: abra o painel em `localhost`, porque o navegador precisa de um contexto seguro para liberar o microfone. E a voz Qwen ainda leva alguns segundos para gerar uma frase nova, o cache ajuda nas falas repetidas, não faz milagre hehe.

&emsp; Para pesquisas Google, coloque uma chave SerpApi em **Conexão**. Sem ela, o Ferris devolve um link de busca. Ele foi feito para conversar e pesquisar, não para programar, editar arquivos ou executar comandos.

---

### Gravando e treinando

&emsp; A aba **Minha voz** é onde preparo os exemplos. Escolha **ESP32** como microfone, dê um nome para a sessão e grave a palavra "Ferris", outras palavras e ruído do ambiente. As gravações de treino vêm do microfone da placa por USB, não do microfone do navegador quando essa opção está selecionada.

1. Grave pelo menos 12 exemplos de Ferris e 12 negativos.
2. Para um primeiro teste na mesma sessão, escolha **Experimental**.
3. Para avaliar melhor, faça pelo menos quatro sessões mudando distância, ambiente ou momento, com positivos e negativos em cada uma.
4. Clique em **Treinar e usar modelo**.

&emsp; O treino roda neste PC, exporta o detector ONNX e os pesos equivalentes em C. Os arquivos ficam em `models/`, e o cabeçalho que vai para a placa fica em `models/model_weights.h`. O modelo anterior continua disponível enquanto o novo treina. O resultado experimental serve para testar, não para dizer que vai funcionar igualmente bem em outro ambiente.

**Para gravar o modelo novo no ESP32:**

```sh
docker compose stop ferris
# Na primeira vez, baixa a imagem do ESP-IDF; ela é grande.
docker compose run --rm firmware
docker compose start ferris
```

&emsp; Separei a gravação do firmware porque o painel já está usando a serial para receber os eventos. Esses comandos liberam a porta, compilam com ESP-IDF 5.4.2, gravam a placa e devolvem o USB para o Ferris. Dentro do Docker, o botão do painel faz o treino; a gravação da placa é essa etapa acima. Não precisa instalar ESP-IDF no sistema.

---

### Como liguei as peças

| Peça | ESP32 |
| --- | --- |
| Microfone VDD | 3V3 |
| Microfone GND e L/R | GND |
| Microfone SD | GPIO 22 |
| Microfone SCK | GPIO 26 |
| Microfone WS | GPIO 25 |
| LED vermelho | GPIO 18, com resistor de 220 a 330 Ω em série |
| LED verde | GPIO 19, com resistor de 220 a 330 Ω em série |
| Botão de mute | GPIO 23 e GND, usando pull-up interno |

&emsp; Os cátodos dos LEDs vão ao GND. O vermelho indica mute, o verde indica escuta e pulsa quando o nome é detectado. O microfone é de **3,3 V**. O botão alterna o mute a cada clique, não precisa ficar segurando.

---

### Onde ficou cada coisa

```text
ferris/             servidor do painel, cliente LM Studio, áudio e worker de voz
  static/           HTML, CSS e JavaScript do painel
  vendor/           adaptador ONNX e sua licença
tools/
  models/           download dos modelos Whisper e TTS
  training/         treino, augmentação, DSP e benchmark
  firmware/         gravação do ESP32 fora do Compose
  testing/          teste do navegador
firmware/           aplicação FreeRTOS e componentes C compartilhados
tests/              testes automatizados
docker/             inicialização, healthcheck e gravação da placa
assets/             diagrama das tarefas
data/               gravações, configurações e modelos de voz, fora do Git
models/             detector treinado e pesos exportados, fora do Git
compose.yaml        painel, voz, firmware e testes
compose.usb.yaml    acesso opcional ao USB no Linux
```

&emsp; No firmware, captura, controles, extração de features, detecção, gravação e envio de eventos ficam em tarefas separadas. A captura passa blocos de áudio para um ring buffer, a extração calcula RMS, centroide e MFCCs, e a detecção exige duas janelas positivas. As filas não seguram a captura esperando rede, e o mute invalida os dados antigos que ainda estavam em trânsito.

![Tarefas do Ferris no FreeRTOS](assets/rtos.svg)

---

### Testes

```sh
docker compose build test
docker compose run --rm test
```

&emsp; A suíte cobre API, mute, gravação, treino, equivalência ONNX/C, conexão remota e cancelamento de voz. Ela usa dados temporários, não treina por cima das minhas gravações. O teste completo do navegador fica em `tools/testing/browser_smoke.mjs`; para desenvolvimento local, com Node e Chromium instalados:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[train,device,voice,tts]'
PYTHON_BIN=.venv/bin/python node tools/testing/browser_smoke.mjs
```

---

### Se alguma coisa não subir

| O que aconteceu | O que conferir |
| --- | --- |
| Porta 8765 ou 8770 ocupada | Pare o processo Python antigo ou ajuste a porta no `.env` |
| USB não encontrado | Confira `FERRIS_SERIAL_PORT`; sem placa, use apenas `compose.yaml` |
| Permissão negada no USB | Confira `FERRIS_SERIAL_GID` com `stat -c '%g' /dev/ttyUSB0` e recrie o container |
| Permissão negada em `data/` ou `models/` | No Linux, ajuste `FERRIS_UID` e `FERRIS_GID` para os valores de `id -u` e `id -g` |
| Whisper ou voz indisponível | Veja `docker compose logs -f voice` no destino; confirme IP, porta e token no painel |
| Timeout nos dois serviços remotos | Confira se o destino está acordado e se o firewall permite 1234 e 8770 |
| Modelo aparece com outro nome | Use **Buscar modelos** e salve o identificador que o LM Studio devolveu |

&emsp; Se quiser exigir autenticação no worker, defina `FERRIS_VOICE_TOKEN` no `.env` do destino e salve o mesmo valor no campo de voz do painel. É separado da chave do LM Studio. O painel é publicado somente em `127.0.0.1`; o worker precisa ficar acessível pela rede para o outro PC conseguir chamar.

### Referências

https://docs.docker.com/compose/how-tos/profiles/

https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign

https://github.com/SYSTRAN/faster-whisper
