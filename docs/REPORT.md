# Relatório técnico — Ferris

**Estado: implementação inicial. Medidas físicas e avaliação de fala real pendentes.**

## Objetivo e escopo

Detectar o padrão acústico “Ferris” no ESP32 para iniciar uma conversa com um assistente remoto. O enunciado permite um padrão acústico de interesse, além de anomalias clássicas; trata-se aqui de keyword spotting binário. A aplicação é interação sem as mãos, com detecção local independente da disponibilidade da IA de conversação.

O PC oferece uma primeira interface utilizável, coleta dados e conecta ao LM Studio já existente em outro computador. Detecção, transcrição, conversa e síntese são etapas distintas. A etapa acadêmica central é o pipeline de captura, features e classificação no FreeRTOS.

## Arquitetura

Veja [o diagrama SVG](rtos.svg) e [a descrição do firmware](../firmware/README.md). As prioridades relativas são captura 5, controles físicos 4, features 3, detecção 2 e rede 1. Não se exige fixação de tarefa em um núcleo específico; o ESP-IDF gerencia o escalonamento. A leitura I2S usa DMA e bloqueia a tarefa até dados estarem disponíveis, sem processamento pesado em ISR.

O ring buffer transfere propriedade temporária dos blocos. A tarefa de features copia a informação antes de devolvê-los. A janela de 16.000 amostras pertence a uma única tarefa. Filas com cópia transferem vetores e eventos. Os contadores têm seção crítica curta; rede e cálculos não seguram locks de aplicação. Em sobrecarga, perdas são explícitas em vez de causar crescimento de memória ou bloqueio da captura.

Mute físico alterna em um botão GPIO 27 com pull-up e debounce de 30 ms. A geração do áudio muda em cada alternância: consumidores descartam dados antigos mesmo após retomar. Só a tarefa de captura controla I2S; depois de silenciar, ela desativa o canal ao sair da leitura corrente. A retomada descarta quatro blocos de DMA e refaz a janela. Os controles indicam captura inativa em vermelho (18) e ativa em verde (19), com pulso de ativação no verde. Testes C no host verificam bounce, botão segurado e rejeição de áudio de gerações antigas; resposta física do botão e LEDs ainda precisa ser medida na placa.

## Modelo

Cada janela de 1 segundo a 16 kHz é dividida em frames de 256 amostras, com salto de 160 amostras. Usa-se janela Hann, FFT radix-2, banco de 20 filtros mel e 13 coeficientes cepstrais. RMS e centroide espectral baseado em potência complementam os MFCCs. As características são agregadas em dez posições temporais, formando 150 valores. A implementação exata está no frontend C compartilhado.

Regressão logística binária regularizada, com pesos de classe balanceados, produz a probabilidade de ativação. O escalonamento aprendido nas features de treino é incorporado aos pesos finais. A representação ONNX é `MatMul → Add → Sigmoid`, opset 17. O firmware executa o mesmo cálculo em C; não há runtime ONNX dentro do ESP32.

O treino separa sessões de gravação completas, rejeita duplicatas exatas e aplica deslocamento temporal apenas no conjunto de treino. O limiar é escolhido na validação, priorizando recall ≥ 0,8 quando possível e reduzindo falsos positivos. O conjunto de teste não escolhe limiar. Esse critério não garante que o baseline atinja a meta: a métrica efetivamente obtida deve ser relatada.

## Evidências disponíveis

Os testes automatizados cobrem a API, credenciais, isolamento de sessões, conversa remota simulada, ferramentas permitidas, erros, validação de WAV, comportamento do DSP, treinamento/exportação ONNX e equivalência C/ONNX. O navegador é testado com Chromium headless, microfone artificial e servidor remoto simulado.

Em 19/09/2026, os 18 testes Python passaram (incluindo debounce e invalidação de áudio no mute) e o firmware em modo diagnóstico foi compilado com sucesso para ESP32 usando a imagem oficial `espressif/idf:v5.4.2`. O binário ocupou `0xbde20` bytes (777.760 bytes), com aproximadamente 26% da partição de aplicação de 1 MiB livres. Compilar não valida a pinagem, captura física ou deadlines no dispositivo. O cabeçalho do modelo real ainda depende da coleta e treinamento.

A execução de teste com tons sintéticos obteve erro absoluto máximo aproximado de **5,97 × 10⁻⁸** entre ONNX Runtime e o classificador C no host. Essa evidência valida a implementação numérica e o pipeline, **não a acurácia de “Ferris”**, a aritmética do alvo físico ou a latência do ESP32. Nenhum resultado sintético deve ser apresentado como resultado de fala real.

## Resultados a preencher com hardware e dados reais

| Medida | Resultado |
|---|---|
| Placa, frequência de CPU, versão de firmware e microfone | Pendente |
| Exemplos positivos/negativos e sessões independentes | Pendente dos áudios |
| Recall, precisão e matriz de confusão em sessões reservadas | Pendente |
| Falsos acionamentos por hora de áudio contínuo negativo | Pendente |
| Captura I2S: p50/p95/p99 de leitura do bloco | Pendente |
| Features e inferência no ESP32: p50/p95/p99 | Pendente |
| Tempo fim da palavra → alerta LED | Pendente |
| Perdas por estágio durante carga e desconexão Wi-Fi | Pendente |
| Heap livre e watermark de stacks | Pendente |
| Pergunta → primeiro áudio da resposta via LM Studio real | Pendente do endpoint remoto |

## Procedimento de avaliação

1. Coletar fala real, negativos difíceis e ruído em pelo menos quatro sessões; incluir diferentes ambientes e o INMP441.
2. Treinar e reservar sessões de teste. Registrar os hashes do ONNX e do frontend.
3. Usar `tools/benchmark.py` com áudio contínuo e anotações dos términos das palavras. Relatar duração, falsos acionamentos/hora e perdas; clipes isolados não estimam adequadamente acionamentos falsos no uso contínuo.
4. Gravar o firmware com os pesos reais. Registrar logs seriais e medir p50/p95/p99 por etapa. Distinguir duração de leitura, processamento, fila, janela de 1 s e confirmação adicional de 250 ms.
5. Repetir com ruído e Wi-Fi desconectado. O LED e a classificação local devem continuar funcionando; eventos de rede perdidos devem ser contados, sem reprodução tardia.
6. Medir a conversa com o servidor remoto real separadamente. A latência de rede/LLM não é latência do detector acústico.

## Limitações e próximos passos

O baseline linear pode confundir palavras semelhantes ou falhar sob mudança de microfone, distância e voz. Caso os dados reais não atinjam as metas, substituir por uma rede compacta de keyword spotting e adaptar sua implantação no ESP32, mantendo o ONNX como referência e medindo equivalência. O sistema não autentica quem está falando.

O painel opera em half-duplex, sem cancelamento de eco sofisticado e sem interrupção por voz durante TTS. A transcrição local depende de um modelo Whisper instalado separadamente. A pesquisa depende do provedor configurado. O ESP32 inicial envia apenas eventos; transporte de perguntas e reprodução de áudio embarcada serão implementados após definir o hardware de saída.

## Rastreabilidade do enunciado

| Requisito | Implementação / evidência |
|---|---|
| Captura ESP32 + INMP441 | `firmware/main/main.c`, tarefa `capture` |
| ≥ 3 tarefas sincronizadas | Captura, features, detecção; tarefa extra de rede |
| Buffer circular via I2S | Ring buffer de blocos e janela circular de 1 segundo |
| Modelo pré-treinado | Pipeline fornecido; treinamento real pendente antes da gravação final |
| Arquivo ONNX | `models/wake.onnx`, gerado pelo treinamento |
| LED/buzzer | LED implementado; buzzer é opcional |
| Latência por etapa | Instrumentação serial e benchmark host; medições físicas pendentes |
| Conflitos de concorrência | Propriedade do buffer, filas de cópia, detecção de lacunas e seção crítica |
| Diagrama em SVG/imagem | `docs/rtos.svg` |
| Código de teste | `tests/`, `tools/benchmark.py`, `tools/browser_smoke.mjs` |

Este relatório é uma base rastreável; não representa uma entrega acadêmica concluída enquanto modelo real, hardware e medições estiverem pendentes.
