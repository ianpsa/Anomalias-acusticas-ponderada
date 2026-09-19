# Ferris

Um assistente que atende por **“Ferris”**, cumprimenta conforme o horário e conversa usando um **LM Studio já configurado em outro PC**. O projeto também implementa a base de um detector de palavra de ativação em ESP32 + INMP441 com FreeRTOS.

Este repositório contém o cliente do LM Studio, o painel, o processamento de áudio e o detector ONNX. Não instala nem hospeda o servidor de IA ou o modelo de conversação.

## Começar pelo PC

Python 3.11+ é suficiente para o painel e a conexão com o servidor remoto, sem dependências Python adicionais:

```bash
python3 -m ferris.server
```

Abra **http://127.0.0.1:8765**. Em **Conexão**, informe o endereço do LM Studio que já roda no outro computador, por exemplo `http://192.168.1.50:1234/v1`, e a chave se o servidor a exigir. Clique em **Buscar modelos**, escolha o identificador e salve. O IP do exemplo é ilustrativo; use o endereço real mostrado pelo seu servidor.

- **Conversa:** texto, fontes da pesquisa e leitura das respostas em português.
- **Chamar Ferris:** testa a saudação contextual sem precisar de microfone ou modelo treinado.
- **Voz do navegador:** reconhece “Ferris” por transcrição e abre uma janela de conversa de 20 segundos. Requer navegador com `SpeechRecognition`, normalmente Chromium/Chrome. O serviço de reconhecimento pode processar áudio online; não é o detector ONNX embarcado.
- **Local: ONNX + Whisper:** usa seu detector treinado e transcrição no PC. Requer os passos de treinamento e voz local abaixo.
- **Parar:** interrompe a fala e desliga o microfone. Depois de uma resposta, Ferris permite outra pergunta sem repetir o nome, até expirar a janela.
- **Minha voz:** grava e importa exemplos para o treinamento. Os áudios e as chaves ficam em `data/`, ignorado pelo Git.

Durante a fala do Ferris, o reconhecimento é suspenso para evitar que ele acione a si mesmo. A primeira versão é half-duplex: para interromper uma resposta falada, use **Parar**. No PC, uma caixa Bluetooth pode ser selecionada como saída normal de áudio do sistema operacional.

O reconhecimento/áudio do navegador exige um contexto seguro: use `localhost` no PC. Para abrir o painel pelo celular ou por outro computador, será necessário HTTPS; servir HTTP pela LAN não habilita o microfone remoto.

## O que já existe e o que falta

| Parte | Estado |
|---|---|
| Painel, conversa por texto e cliente HTTP do LM Studio | Implementados; integração testada com servidor remoto simulado |
| Saudações por horário e nome | Implementadas; independentes da disponibilidade do LM Studio |
| Pesquisa Google | Integração SerpApi implementada; depende de chave e acesso ao provedor |
| Gravação de exemplos WAV e pipeline de treinamento | Implementados |
| Inferência ONNX no PC e exportação equivalente em C | Implementadas; equivalência numérica testada |
| Modelo pessoal que reconhece “Ferris” | **Pendente dos áudios reais; nenhum modelo de voz fictício é incluído** |
| Firmware FreeRTOS | Compilação ESP32 com ESP-IDF 5.4.2 validada; captura, features, inferência, LED e evento implementados; validação física pendente |
| Conversa inteiramente pelo ESP32, com microfone e saída própria | Próxima etapa: transportar a pergunta e reproduzir a resposta após definir o hardware de saída |

O evento do ESP32 aciona a saudação no painel aberto do PC. Nesta fase, a pergunta seguinte e a reprodução da resposta usam o áudio do PC. Não há implementação de alto-falante Bluetooth no ESP32.

## Pesquisa e capacidades do Ferris

O serviço local oferece ao modelo apenas `web_search`. Para pesquisar resultados Google automaticamente, forneça uma chave **SerpApi** em Conexão. A consulta é enviada ao provedor e os resultados entram como dados para o modelo remoto; as fontes aparecem no painel. Sem chave, um pedido de pesquisa oferece um link de busca no Google, sem inventar resultados.

Ferris não possui ferramentas de terminal, edição de arquivos ou execução de código. O cliente também orienta o modelo a recusar programação e bloqueia pedidos comuns de código. Esse filtro de texto não é uma garantia de que um LLM jamais produzirá um trecho de código; a ausência de ferramentas executáveis é o limite efetivo de capacidade.

Conversas ficam apenas em memória, em sessões separadas por aba e com histórico limitado. Configurações persistem em `data/settings.json`, com permissão local `0600`. Áudio do modo local não é enviado ao LM Studio: somente texto da conversa e, quando aplicável, resultados da pesquisa.

## Treinar o detector ONNX

O detector usa RMS, centroide espectral e 13 MFCCs em dez intervalos temporais, totalizando **150 features por janela de 1 segundo**. O frontend C é compartilhado entre o treinamento no PC e o firmware. O modelo inicial é uma regressão logística regularizada, compacta o suficiente para exportar sua inferência ao ESP32; sua qualidade deve ser medida com fala real.

Em **Minha voz**, colete as classes `Ferris`, `Outras palavras` e `Ambiente`. Grave a palavra uma vez no centro de cada clipe. Use várias distâncias, intensidades e ambientes. Inclua palavras parecidas, como “férias” e “feliz”, conversas normais, TV, silêncio e ruídos. Idealmente inclua gravações feitas pelo próprio INMP441 para reduzir diferenças entre microfones.

Mude o campo **Sessão de gravação** ao mudar de dia ou ambiente. São exigidas no mínimo quatro sessões diferentes, contendo positivos e negativos em cada uma, e pelo menos 12 positivos e 12 negativos no total. Esse mínimo serve para executar o pipeline; comece com dezenas ou centenas de exemplos variados para avaliar utilidade real. Um detector treinado só com sua voz não garante reconhecer outras pessoas, nem funciona como autenticação de identidade.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[train]'
python tools/train_wake.py
```

Requer compilador C (`cc`/GCC) para compilar a extração compartilhada. Python 3.11–3.13 é recomendado para os extras com bibliotecas nativas. O pipeline:

1. Valida WAV mono, PCM 16 bits, 16 kHz e rejeita duplicatas.
2. Separa **sessões inteiras** em treino, validação e teste antes de extrair janelas.
3. Treina com aumento por deslocamento temporal somente no treino.
4. Escolhe o limiar no conjunto de validação e avalia o teste reservado.
5. Gera `models/wake.onnx`, `models/wake.json` e `firmware/main/model_weights.h`.
6. Compara a probabilidade ONNX com a implementação C, exigindo erro absoluto ≤ `1e-4`.

O ONNX recebe `features: float32[batch, 150]` e retorna `probability: float32[batch, 1]`. O PC executa o arquivo com ONNX Runtime. O ESP32 executa os mesmos pesos exportados como produto escalar + sigmoide; não executa ONNX Runtime. Ambos usam o mesmo extrator C. O arquivo ONNX é o artefato de modelo da entrega.

Reinicie o serviço Ferris após treinar novamente. Arquivos gerados são ignorados pelo Git; após validar o modelo real, publique os artefatos de forma deliberada para a entrega, sem incluir seus áudios privados.

Para importar gravações em outro formato, converta-as e recorte clipes de até 5 segundos. Exemplo de conversão:

```bash
ffmpeg -i exemplo.m4a -ac 1 -ar 16000 -c:a pcm_s16le exemplo.wav
```

## Voz local no PC

O modo local precisa de um modelo Whisper no formato CTranslate2 já disponível em disco. Ele é usado exclusivamente para transcrição, não substitui o LM Studio remoto. O serviço não baixa modelos automaticamente.

```bash
source .venv/bin/activate
python -m pip install -e '.[voice]'
export FERRIS_WHISPER_MODEL=/caminho/para/modelo-whisper-local
python -m ferris.server
```

Depois de treinar o ONNX e configurar o Whisper, selecione **Local: ONNX + Whisper** e ative o microfone. A detecção exige duas janelas positivas consecutivas. Depois da saudação, a captura da pergunta termina com aproximadamente 850 ms de silêncio ou no limite de 12 segundos. O limiar inicial de voz é fixo e precisa ser ajustado se o ambiente ou microfone exigir. A síntese usa uma voz pt-BR do navegador/sistema, preferindo voz local quando disponível.

## ESP32 e FreeRTOS

Consulte [firmware/README.md](firmware/README.md) para pinagem, configuração e compilação. O alvo inicial é o **ESP32 original**, com INMP441 e LED. A placa exata e a saída de áudio ainda precisam ser confirmadas.

![Tarefas e sincronização FreeRTOS](docs/rtos.svg)

O microcontrolador envia eventos ao serviço **Ferris no PC**, não diretamente ao LM Studio. Para receber eventos na LAN:

```bash
export FERRIS_TOKEN='defina-um-token-de-painel-com-24-ou-mais-caracteres'
export FERRIS_DEVICE_TOKEN='defina-outro-token-para-o-esp32'
python3 -m ferris.server --host 0.0.0.0
```

Informe `FERRIS_TOKEN` em **Conexão → Pesquisa Google e acesso pela rede** no painel. Configure o mesmo `FERRIS_DEVICE_TOKEN` no firmware. O token do dispositivo só autoriza eventos de ativação; não dá acesso às configurações do painel. Use rede confiável para o transporte HTTP inicial. O endereço do evento será `http://IP_DO_PC_FERRIS:8765/api/device/wake`.

## Validação

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
node tools/browser_smoke.mjs
```

O segundo comando precisa de Chromium e Node.js. Usa um microfone artificial e um servidor HTTP simulado para testar a interface, sem conectar a um modelo real ou capturar sua voz.

Para avaliar áudio contínuo com o modelo real:

```bash
python tools/benchmark.py teste-continuo.wav --events eventos.json
```

`eventos.json` contém uma lista com os instantes em segundos em que cada palavra termina, por exemplo `[2.4, 8.1, 15.6]`. Para áudio exclusivamente negativo, use `[]`. O script mede p50/p95/p99 de features e inferência no **PC**, perdas, falsos acionamentos por hora e atraso até ativação. O relatório não pode ser apresentado como latência do ESP32. Para o dispositivo, use as métricas reais da serial descritas no [relatório técnico](docs/REPORT.md).

Os testes automatizados usam tons sintéticos para verificar o fluxo, a separação de dados e a equivalência ONNX/C. Seus resultados não medem acurácia da palavra “Ferris”.

## Organização

- `ferris/`: serviço local, cliente LM Studio, voz e painel.
- `firmware/`: projeto ESP-IDF e frontend DSP compartilhado.
- `tools/`: treinamento, benchmark e teste de navegador.
- `tests/`: testes de integração e do modelo.
- `docs/`: enunciado, arquitetura, relatório e recuperação do contexto.

O contexto original foi reconstruído a partir do pedido salvo no banco do Codex. O arquivo antigo de sessão tinha **0 bytes** e não foi sobrescrito. Detalhes em [docs/RECOVERY.md](docs/RECOVERY.md).

## Referências

- [LM Studio: acesso pela rede](https://lmstudio.ai/docs/developer/core/server/serve-on-network) e [ferramentas na API compatível](https://lmstudio.ai/docs/developer/openai-compat/tools).
- [ESP-IDF 5.4.2: I2S](https://docs.espressif.com/projects/esp-idf/en/v5.4.2/esp32/api-reference/peripherals/i2s.html).
- [Espressif: suporte a áudio Bluetooth por chip](https://docs.espressif.com/projects/esp-adf/en/latest/solution-center/bluetooth-audio.html). O ESP32-S3 não suporta Bluetooth Classic/A2DP; não assuma que uma caixa Bluetooth funcionará nele.
- [MDN: SpeechRecognition](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition) e [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
