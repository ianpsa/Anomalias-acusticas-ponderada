# Artefatos do detector

`tools/train_wake.py` gera:

- `wake.onnx`: entrada `features` float32 `[batch, 150]`; saída `probability` float32 `[batch, 1]`.
- `wake.json`: limiar, pesos, métricas, grupos de treino/validação/teste e hashes de modelo/DSP/dados.
- `../firmware/main/model_weights.h`: os mesmos pesos para o ESP32.

Não há um modelo Ferris pré-treinado neste repositório inicial: ele depende das gravações reais. Não use os modelos sintéticos criados pelos testes como detector de fala. A coleta com uma única pessoa também não caracteriza autenticação de identidade.

As versões geradas são ignoradas por padrão. Após avaliar um modelo real, selecione explicitamente os artefatos para a entrega/release; revise os caminhos e metadados locais de `wake.json` antes de publicar. Guarde treino, validação e teste separados por sessão de gravação.
