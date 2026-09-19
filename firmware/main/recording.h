// USB dataset capture: the capture task remains the sole I2S owner.
#include <fcntl.h>
#include <unistd.h>
#include "mbedtls/base64.h"
#include "freertos/semphr.h"
#define RECORD_SAMPLES 32000
static int16_t record_pcm[RECORD_SAMPLES];
static SemaphoreHandle_t record_mutex;
static char record_id[33];
static unsigned record_count, record_sent;
static uint32_t record_generation, record_crc;
static bool recording;
static uint32_t boot_id;

static bool collection_active(void) {
    portENTER_CRITICAL(&stats_mux); bool active = recording; portEXIT_CRITICAL(&stats_mux);
    return active;
}
static void collection_set(bool active) {
    portENTER_CRITICAL(&stats_mux); recording = active; portEXIT_CRITICAL(&stats_mux);
}
static void collect_chunk(const int16_t *pcm, uint32_t generation) {
    xSemaphoreTake(record_mutex, portMAX_DELAY);
    if (collection_active() && record_generation == generation && record_count < RECORD_SAMPLES) {
        memcpy(record_pcm + record_count, pcm, CHUNK * sizeof(int16_t));
        record_count += CHUNK;
    }
    xSemaphoreGive(record_mutex);
}
static void state_body(char *body, size_t size) {
    ferris_listen_state_t s = listening_snapshot();
    snprintf(body, size, "{\"device\":\"esp32-ferris\",\"type\":\"state\",\"boot_id\":\"%08"PRIx32"\",\"generation\":%"PRIu32",\"muted\":%s,\"capture_active\":%s}",
             boot_id, s.generation, s.muted ? "true" : "false", !s.muted && s.ready ? "true" : "false");
}
static void record_command(const char *line) {
    bool start = strncmp(line, "FERRIS_RECORD ", 14) == 0;
    bool cancel = strncmp(line, "FERRIS_CANCEL ", 14) == 0;
    if ((!start && !cancel) || strlen(line + 14) != 32 || strspn(line + 14, "0123456789abcdef") != 32) return;
    bool rejected = false, started = false;
    xSemaphoreTake(record_mutex, portMAX_DELAY);
    if (cancel) {
        if (strcmp(record_id, line + 14) == 0) collection_set(false);
    } else {
        ferris_listen_state_t s = listening_snapshot();
        if (collection_active() || s.muted || !s.ready) {
            rejected = true;
        } else {
            strcpy(record_id, line + 14); record_count = record_sent = 0;
            record_generation = s.generation; record_crc = UINT32_MAX;
            collection_set(true);
            started = true;
        }
    }
    xSemaphoreGive(record_mutex);
    if (rejected) printf("FERRIS_AUDIO_ERROR %s unavailable\n", line + 14);
    if (started) printf("FERRIS_RECORD_BEGIN %s\n", line + 14);
}
static void recording_task(void *arg) {
    (void)arg;
    fcntl(STDIN_FILENO, F_SETFL, O_NONBLOCK);
    char command[80]; unsigned used = 0;
    while (1) {
        char ch;
        while (read(STDIN_FILENO, &ch, 1) == 1) {
            if (ch == '\n') { command[used] = 0; record_command(command); used = 0; }
            else if (ch != '\r') { if (used < sizeof(command)-1) command[used++] = ch; else used = 0; }
        }
        unsigned char encoded[1100]; size_t length = 0;
        char id[33]; unsigned sequence = 0; bool send = false, failed = false, done = false;
        uint32_t crc = 0;
        xSemaphoreTake(record_mutex, portMAX_DELAY);
        if (collection_active()) {
            strcpy(id, record_id);
            ferris_listen_state_t s = listening_snapshot();
            if (s.muted || s.generation != record_generation) { collection_set(false); failed = true; }
            else if (record_count == RECORD_SAMPLES) {
                const unsigned char *bytes = (unsigned char *)(record_pcm + record_sent);
                for (unsigned i = 0; i < 800; i++) {
                    record_crc ^= bytes[i];
                    for (unsigned bit = 0; bit < 8; bit++) record_crc = (record_crc >> 1) ^ (0xedb88320U & (0U-(record_crc & 1U)));
                }
                int result = mbedtls_base64_encode(encoded, sizeof(encoded), &length, bytes, 800);
                configASSERT(result == 0); encoded[length] = 0;
                sequence = record_sent / 400; record_sent += 400; send = true;
                done = record_sent == RECORD_SAMPLES; crc = record_crc ^ UINT32_MAX;
            }
        }
        xSemaphoreGive(record_mutex);
        // UART writes never hold the recording mutex or an RTOS critical section.
        if (failed) printf("FERRIS_AUDIO_ERROR %s muted\n", id);
        if (send) {
            printf("FERRIS_AUDIO %s %u %s\n", id, sequence, encoded);
            if (done) {
                printf("FERRIS_AUDIO_END %s 64000 %08"PRIx32"\n", id, crc);
                xSemaphoreTake(record_mutex, portMAX_DELAY); collection_set(false); xSemaphoreGive(record_mutex);
            }
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
