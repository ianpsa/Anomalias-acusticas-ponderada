#include "ferris_dsp.h"
#include <math.h>
#include <string.h>

#define N 256
#define MEL 20
#define CEPS 13
#define HOP 160
#define PI 3.14159265358979323846f

/* Pre-enfase classica antes da FFT: compensa a queda de cerca de 6 dB por
   oitava da voz e realca as formantes que separam "Ferris" de "ferias". O RMS
   continua sendo medido sem ela, para seguir sendo energia e nao espectro. */
#define PREEMPHASIS .97f

static float hann[N];
static float dct[CEPS][MEL];
static int edges[MEL + 2];
static int tables_ready;

/* Construidas na primeira chamada. Antes elas eram refeitas a cada janela, o que
   custava cerca de 26 mil cosf por segundo de audio. Duas chamadas simultaneas
   escreveriam os mesmos valores, entao a corrida e inofensiva. */
static void build_tables(void) {
    for (int i = 0; i < N; i++) hann[i] = .5f - .5f * cosf(2 * PI * i / (N - 1));
    float melmax = 2595 * log10f(1 + 8000.f / 700);
    for (int i = 0; i < MEL + 2; i++) {
        float hz = 700 * (powf(10, melmax * i / (MEL + 1) / 2595) - 1);
        edges[i] = (int)floorf((N + 1) * hz / 16000);
        if (edges[i] > N / 2) edges[i] = N / 2;
    }
    for (int c = 0; c < CEPS; c++)
        for (int m = 0; m < MEL; m++)
            dct[c][m] = cosf(PI * c * (m + .5f) / MEL) / MEL;
    tables_ready = 1;
}

static void fft(float *re, float *im) {
    for (unsigned i = 1, j = 0; i < N; i++) {
        unsigned bit = N >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) { float t = re[i]; re[i] = re[j]; re[j] = t; }
    }
    for (int len = 2; len <= N; len <<= 1) {
        float wr0 = cosf(-2 * PI / len), wi0 = sinf(-2 * PI / len);
        for (int i = 0; i < N; i += len) {
            float wr = 1, wi = 0;
            for (int j = 0; j < len / 2; j++) {
                int a = i + j, b = a + len / 2;
                float tr = wr * re[b] - wi * im[b], ti = wr * im[b] + wi * re[b];
                re[b] = re[a] - tr; im[b] = im[a] - ti; re[a] += tr; im[a] += ti;
                float next = wr * wr0 - wi * wi0; wi = wr * wi0 + wi * wr0; wr = next;
            }
        }
    }
}

void ferris_features(const int16_t *pcm, float *out) {
    float re[N], im[N], power[N / 2 + 1], mel[MEL];
    int counts[10] = {0};
    if (!tables_ready) build_tables();
    memset(out, 0, sizeof(float) * FERRIS_FEATURES);
    /* Retirar a media do bloco elimina o offset do INMP441, que entrava inteiro
       no RMS e puxava o centroide para baixo. Em bloco fixo isso e um passa-altas
       exato, sem estado entre janelas e sem buffer extra. A soma vai em int32
       porque 16000 amostras de 16 bits nao cabem na mantissa de um float. */
    int32_t offset = 0;
    for (int i = 0; i < FERRIS_SAMPLES; i++) offset += pcm[i];
    const float dc = (float)offset / FERRIS_SAMPLES;
    const int frames = (FERRIS_SAMPLES - N) / HOP + 1;
    for (int frame = 0; frame < frames; frame++) {
        float energy = 0;
        for (int i = 0; i < N; i++) {
            const int n = frame * HOP + i;
            const float x = (pcm[n] - dc) / 32768.f;
            const float previous = n > 0 ? (pcm[n - 1] - dc) / 32768.f : x;
            energy += x * x;
            re[i] = (x - PREEMPHASIS * previous) * hann[i];
            im[i] = 0;
        }
        fft(re, im);
        float total = 0, centroid = 0;
        for (int i = 0; i <= N / 2; i++) {
            power[i] = (re[i]*re[i] + im[i]*im[i]) / N;
            total += power[i]; centroid += power[i] * i * (16000.f / N);
        }
        for (int m = 0; m < MEL; m++) {
            float sum = 0;
            for (int k = edges[m]; k < edges[m+1]; k++)
                sum += power[k] * (k - edges[m]) / (float)(edges[m+1] - edges[m]);
            for (int k = edges[m+1]; k < edges[m+2]; k++)
                sum += power[k] * (edges[m+2] - k) / (float)(edges[m+2] - edges[m+1]);
            mel[m] = logf(fmaxf(sum, 1e-10f));
        }
        int bin = frame * 10 / frames; counts[bin]++;
        out[bin*15] += sqrtf(energy / N);
        out[bin*15+1] += total > 1e-10f ? centroid / total / 8000 : 0;
        for (int c = 0; c < CEPS; c++) {
            float value = 0;
            for (int m = 0; m < MEL; m++) value += mel[m] * dct[c][m];
            out[bin*15+2+c] += value;
        }
    }
    for (int b = 0; b < 10; b++)
        for (int i = 0; i < 15; i++) out[b*15+i] /= counts[b];
}

float ferris_predict(const float *features, const float *weights, float bias) {
    for (int i = 0; i < FERRIS_FEATURES; i++) bias += features[i] * weights[i];
    if (bias >= 0) return 1 / (1 + expf(-bias));
    float e = expf(bias); return e / (1 + e);
}

float ferris_predict_hidden(const float *features, const float *weights,
                           const float *hidden_bias, const float *output_weights, float bias) {
    for (int h = 0; h < FERRIS_HIDDEN; h++) {
        float value = hidden_bias[h];
        for (int i = 0; i < FERRIS_FEATURES; i++) value += features[i] * weights[i * FERRIS_HIDDEN + h];
        bias += fmaxf(0, value) * output_weights[h];
    }
    if (bias >= 0) return 1 / (1 + expf(-bias));
    float e = expf(bias); return e / (1 + e);
}
