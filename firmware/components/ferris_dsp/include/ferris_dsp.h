#pragma once
#include <stdint.h>
#define FERRIS_SAMPLES 16000
#define FERRIS_FEATURES 150
/* Um segundo de PCM 16 bits com sinal, a 16 kHz. Nao aloca nada. As tabelas
   sao montadas na primeira chamada; chamadas simultaneas escrevem os mesmos
   valores, entao a corrida e inofensiva. */
void ferris_features(const int16_t *pcm, float *features);
float ferris_predict(const float *features, const float *weights, float bias);
