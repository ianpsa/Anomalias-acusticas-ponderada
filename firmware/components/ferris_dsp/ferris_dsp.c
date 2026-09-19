#include "ferris_dsp.h"
#include <math.h>
#include <string.h>

#define N 256
#define MEL 20
#define PI 3.14159265358979323846f

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
    float window[N], re[N], im[N], power[N / 2 + 1], mel[MEL];
    int edges[MEL + 2], counts[10] = {0};
    memset(out, 0, sizeof(float) * FERRIS_FEATURES);
    for (int i = 0; i < N; i++) window[i] = .5f - .5f * cosf(2 * PI * i / (N - 1));
    float melmax = 2595 * log10f(1 + 8000.f / 700);
    for (int i = 0; i < MEL + 2; i++) {
        float hz = 700 * (powf(10, melmax * i / (MEL + 1) / 2595) - 1);
        edges[i] = (int)floorf((N + 1) * hz / 16000);
        if (edges[i] > N / 2) edges[i] = N / 2;
    }
    const int frames = (FERRIS_SAMPLES - N) / 160 + 1;
    for (int frame = 0; frame < frames; frame++) {
        float energy = 0;
        for (int i = 0; i < N; i++) {
            float x = pcm[frame * 160 + i] / 32768.f;
            energy += x * x; re[i] = x * window[i]; im[i] = 0;
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
        for (int c = 0; c < 13; c++) {
            float value = 0;
            for (int m = 0; m < MEL; m++) value += mel[m] * cosf(PI * c * (m + .5f) / MEL);
            out[bin*15+2+c] += value / MEL;
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
