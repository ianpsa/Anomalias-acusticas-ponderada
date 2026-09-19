# Artefatos do detector

O **Gemma no LM Studio** cuida da conversa e fica no diretório de modelos do LM Studio, fora deste repositório. Ele não precisa ser treinado com sua voz. Os arquivos abaixo são exclusivamente do detector ONNX que aprende a reconhecer a palavra “Ferris”; a coleta e o treinamento funcionam mesmo com o LM Studio desligado.

`tools/train_wake.py` gera:

- `wake.onnx`: entrada `features` float32 `[batch, 150]`; saída `probability` float32 `[batch, 1]`.
- `wake.json`: limiar, pesos, métricas, grupos de treino/validação/teste e hashes de modelo/DSP/dados.
- `../firmware/main/model_weights.h`: os mesmos pesos para o ESP32.

O botão **Treinar e usar modelo** gera versões em `models/runs/<id>/`, valida a execução e troca `models/active.json` para ativar a versão nova. Ele também atualiza o cabeçalho do firmware. Com a opção USB marcada, compila e grava a placa; sem essa opção, o cabeçalho exportado aguarda a próxima gravação. As métricas da versão ativa aparecem no painel. Falhas no treino mantêm o detector anterior.

Não há um modelo Ferris pré-treinado neste repositório inicial: ele depende das gravações reais. Não use os modelos sintéticos criados pelos testes como detector de fala. A coleta com uma única pessoa também não caracteriza autenticação de identidade.

`--split-mode recordings` permite um primeiro modelo experimental a partir de uma única sessão. Confira `split_mode` e `limitations` em `wake.json`: métricas desse modo usam arquivos reservados, mas não avaliam novos ambientes ou dias. `split_recordings` registra os hashes dos arquivos de cada conjunto; as métricas são por janela de áudio, não por acionamento contínuo.

As versões geradas são ignoradas por padrão. Após avaliar um modelo real, selecione explicitamente os artefatos para a entrega/release; revise os caminhos e metadados locais de `wake.json` antes de publicar. Guarde treino, validação e teste separados por sessão de gravação.
