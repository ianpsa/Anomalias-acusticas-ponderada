# Ferris — Detector de padrões acústicos

Projeto da ponderada de RTOS descrita em [assignment.pdf](assignment.pdf).

O padrão escolhido é a palavra de ativação **Ferris**, usada para acionar um assistente de voz. A aplicação prática é permitir interação sem as mãos, mantendo a detecção local no ESP32. O enunciado permite qualquer padrão acústico de interesse; palavra de ativação é classificação supervisionada de um padrão, não detecção estatística genérica de anomalias.

O ESP32 com INMP441 captura áudio continuamente e executa tarefas FreeRTOS de captura, extração de características e detecção. O computador local fornece controles e áudio na primeira fase. A conversa é atendida por **Gemma no LM Studio, no mesmo PC do Ferris**. O LM Studio é instalado separadamente; este repositório implementa o cliente HTTP e documenta sua configuração local. Um servidor em outro computador continua sendo uma opção pela URL de conexão.

O detector terá um artefato ONNX treinado com exemplos reais. O Ferris pode conversar e pesquisar, mas não fornece ferramentas para programar ou executar comandos.

Requisitos acadêmicos: pelo menos três tarefas sincronizadas, modelo treinado, alerta por LED/buzzer, medições de latência, diagrama de concorrência, relatório e script de teste. O PDF informa entrega em 18/09/2026 e demonstração em 21/09/2026.

`main` é a branch principal. A implementação inicial é desenvolvida em `feat/ferris-assistant`.
