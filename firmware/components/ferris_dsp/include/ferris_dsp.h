#pragma once
#include <stdint.h>
#define FERRIS_SAMPLES 16000
#define FERRIS_FEATURES 150
/* Reentrant, allocation-free. One second of signed 16-bit PCM, 16 kHz. */
void ferris_features(const int16_t *pcm, float *features);
float ferris_predict(const float *features, const float *weights, float bias);
