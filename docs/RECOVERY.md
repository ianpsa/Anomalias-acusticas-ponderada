# Recuperação do trabalho

Foi verificada a sessão Codex `01a0affd-0dda-7573-9663-3058180357bb` indicada pelo usuário. Seu arquivo `rollout` estava presente, mas tinha **0 bytes**. Não havia turnos ou itens correspondentes no banco de histórico consultado.

O banco de metadados ainda conservava o título **Add Ferris wake word detection** e a primeira mensagem. Essa mensagem descrevia uma palavra de ativação semelhante à Alexa, preferencialmente “Ferris”, detectada pelo ESP32, que acionaria uma IA em outro dispositivo por IP e cumprimentaria Ian conforme o horário.

O contexto foi retomado nesta conversa usando essa mensagem, o PDF e as instruções atuais. A transcrição completa da sessão vazia não foi recuperada, e o arquivo de sessão e os bancos do Codex não foram alterados. A cópia local do registro recuperado está em `data/recovered-context.json`, ignorada pelo Git.

Decisões confirmadas pelo usuário:

- Pode implementar código neste repositório; o **assistente Ferris** é que não deve programar.
- Usar modelos ONNX para o detector.
- O servidor LM Studio já existe em outro PC; implementar apenas o cliente de integração aqui.
- Publicar em `ianpsa/Anomalias-acusticas-ponderada`, com `main` e uma branch de implementação.

Continuar a tarefa em uma sessão nova resolve a retomada operacional; não faz o antigo arquivo vazio voltar a conter seu histórico.
