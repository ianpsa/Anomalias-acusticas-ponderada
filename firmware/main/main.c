#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/ringbuf.h"
#include "freertos/event_groups.h"
#include "driver/i2s_std.h"
#include "driver/gpio.h"
#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "nvs_flash.h"
#include "ferris_dsp.h"
#include "ferris_controls.h"

#if __has_include("model_weights.h")
#include "model_weights.h"
#else
#define FERRIS_MODEL_READY 0
#define FERRIS_THRESHOLD 1.0f
static const float ferris_weights[FERRIS_FEATURES] = {0};
static const float ferris_bias = 0;
#endif

#define CHUNK 800
#define WIFI_READY BIT0
static const char *TAG = "ferris";
static i2s_chan_handle_t rx;
static RingbufHandle_t audio_ring;
static QueueHandle_t feature_queue, wake_queue;
static EventGroupHandle_t network;
static portMUX_TYPE stats_mux = portMUX_INITIALIZER_UNLOCKED;
static uint32_t dropped_audio, dropped_features, dropped_network;
static ferris_listen_state_t listen_state;

typedef struct {
    uint32_t sequence;
    uint32_t generation;
    int64_t completed_us;
    int64_t capture_us;
    int16_t pcm[CHUNK];
} audio_chunk_t;
typedef struct {
    float features[FERRIS_FEATURES];
    int64_t completed_us, capture_us, features_us;
    uint32_t sequence;
    uint32_t generation;
} feature_item_t;
typedef struct {
    float confidence;
    uint32_t generation;
    int64_t capture_us, features_us, inference_us, decision_us;
} wake_item_t;

static void increment(uint32_t *value) {
    portENTER_CRITICAL(&stats_mux); (*value)++; portEXIT_CRITICAL(&stats_mux);
}

static ferris_listen_state_t listening_snapshot(void) {
    ferris_listen_state_t snapshot;
    portENTER_CRITICAL(&stats_mux); snapshot = listen_state; portEXIT_CRITICAL(&stats_mux);
    return snapshot;
}

static bool accepts_generation(uint32_t generation) {
    ferris_listen_state_t state = listening_snapshot();
    return ferris_listen_accepts(&state, generation);
}

static void controls_task(void *arg) {
    (void)arg;
    ferris_button_t button;
    ferris_button_init(&button, gpio_get_level(CONFIG_FERRIS_MUTE_BUTTON) == 0, esp_timer_get_time());
    while (1) {
        int64_t now = esp_timer_get_time();
        if (ferris_button_update(&button, gpio_get_level(CONFIG_FERRIS_MUTE_BUTTON) == 0, now)) {
            portENTER_CRITICAL(&stats_mux);
            ferris_listen_toggle(&listen_state);
            bool muted = listen_state.muted;
            portEXIT_CRITICAL(&stats_mux);
            ESP_LOGI(TAG, "{\"muted\":%s}", muted ? "true" : "false");
        }
        ferris_listen_state_t state = listening_snapshot();
        bool active = !state.muted && state.ready;
        gpio_set_level(CONFIG_FERRIS_LED_RED, !active);
        // A short dark pulse on green signals a detected keyword; red means inactive.
        gpio_set_level(CONFIG_FERRIS_LED_GREEN, active && now >= state.alert_until_us);
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

static void capture_task(void *arg) {
    (void)arg;
    int32_t raw[CHUNK]; audio_chunk_t chunk = {0};
    bool running = false;
    uint32_t generation = 0;
    unsigned warmup = 0;
    while (1) {
        ferris_listen_state_t state = listening_snapshot();
        if (state.generation != generation || state.muted) {
            // Only this task owns enable/disable/read of I2S.
            if (running) ESP_ERROR_CHECK(i2s_channel_disable(rx));
            running = false;
            generation = state.generation;
        }
        if (state.muted) { vTaskDelay(pdMS_TO_TICKS(10)); continue; }
        if (!running) {
            ESP_ERROR_CHECK(i2s_channel_enable(rx)); running = true;
            // Discard more than the 8 x 256-sample DMA capacity on resume.
            warmup = 4;
        }
        size_t bytes = 0; int64_t begin = esp_timer_get_time();
        esp_err_t err = i2s_channel_read(rx, raw, sizeof(raw), &bytes, 200);
        chunk.sequence++; /* Gaps identify drops and read failures. */
        if (err != ESP_OK || bytes != sizeof(raw)) {
            increment(&dropped_audio);
            portENTER_CRITICAL(&stats_mux); listen_state.ready = false; portEXIT_CRITICAL(&stats_mux);
            continue;
        }
        state = listening_snapshot();
        if (state.muted || state.generation != generation) continue;
        if (warmup) { warmup--; continue; }
        portENTER_CRITICAL(&stats_mux);
        bool ready = ferris_listen_ready(&listen_state, generation);
        portEXIT_CRITICAL(&stats_mux);
        if (!ready) continue;
        chunk.generation = generation;
        chunk.completed_us = esp_timer_get_time(); chunk.capture_us = chunk.completed_us - begin;
        // INMP441 24-bit signed samples are MSB-aligned in a 32-bit I2S slot.
        for (int i = 0; i < CHUNK; i++) chunk.pcm[i] = (int16_t)(raw[i] >> 16);
        // Never wait on downstream computation or network. Drop newest if full.
        if (xRingbufferSend(audio_ring, &chunk, sizeof(chunk), 0) != pdTRUE) increment(&dropped_audio);
    }
}

static void feature_task(void *arg) {
    (void)arg;
    static int16_t history[FERRIS_SAMPLES], ordered[FERRIS_SAMPLES];
    unsigned cursor = 0, filled = 0, since = 0; uint32_t previous = 0, generation = 0;
    feature_item_t item;
    while (1) {
        size_t size;
        audio_chunk_t *chunk = xRingbufferReceive(audio_ring, &size, portMAX_DELAY);
        if (!chunk) continue;
        if (size != sizeof(*chunk)) { vRingbufferReturnItem(audio_ring, chunk); continue; }
        if (!accepts_generation(chunk->generation)) {
            cursor = filled = since = previous = 0;
            vRingbufferReturnItem(audio_ring, chunk); continue;
        }
        if (generation != chunk->generation || (previous && chunk->sequence != previous + 1)) { cursor = filled = since = 0; }
        generation = chunk->generation;
        previous = chunk->sequence;
        memcpy(&history[cursor], chunk->pcm, sizeof(chunk->pcm));
        cursor = (cursor + CHUNK) % FERRIS_SAMPLES;
        filled = filled + CHUNK > FERRIS_SAMPLES ? FERRIS_SAMPLES : filled + CHUNK;
        since += CHUNK;
        item.completed_us = chunk->completed_us; item.capture_us = chunk->capture_us; item.sequence = chunk->sequence;
        item.generation = chunk->generation;
        vRingbufferReturnItem(audio_ring, chunk); // Ownership returned BEFORE DSP.
        if (filled < FERRIS_SAMPLES || since < 4000) continue;
        since = 0;
        int64_t start = esp_timer_get_time();
        memcpy(ordered, &history[cursor], (FERRIS_SAMPLES-cursor)*sizeof(int16_t));
        memcpy(&ordered[FERRIS_SAMPLES-cursor], history, cursor*sizeof(int16_t));
        ferris_features(ordered, item.features); item.features_us = esp_timer_get_time()-start;
        if (!accepts_generation(item.generation)) continue;
        if (xQueueSend(feature_queue, &item, 0) != pdTRUE) increment(&dropped_features);
    }
}

static void detect_task(void *arg) {
    (void)arg;
    feature_item_t item; unsigned hits = 0; uint32_t previous = 0, generation = 0;
    int64_t cooldown = 0, last_log = 0;
    while (1) {
        if (xQueueReceive(feature_queue, &item, pdMS_TO_TICKS(50)) != pdTRUE) continue;
        if (!accepts_generation(item.generation)) { hits = previous = 0; cooldown = 0; continue; }
        if (generation != item.generation) { hits = previous = 0; cooldown = 0; }
        generation = item.generation;
        int64_t start = esp_timer_get_time();
        if (previous && item.sequence != previous + 5) hits = 0;
        previous = item.sequence;
        float score = FERRIS_MODEL_READY ? ferris_predict(item.features, ferris_weights, ferris_bias) : 0;
        int64_t end = esp_timer_get_time();
        hits = score >= FERRIS_THRESHOLD ? hits+1 : 0;
        if (FERRIS_MODEL_READY && hits >= 2 && end >= cooldown) {
            portENTER_CRITICAL(&stats_mux);
            bool accepted = ferris_listen_alert(&listen_state, generation, end);
            portEXIT_CRITICAL(&stats_mux);
            if (!accepted) { hits = 0; continue; }
            cooldown = end+3000000; hits = 0;
            wake_item_t wake = {.confidence=score, .generation=generation, .capture_us=item.capture_us, .features_us=item.features_us,
                                .inference_us=end-start, .decision_us=end-item.completed_us};
            if (xQueueSend(wake_queue, &wake, 0) != pdTRUE) increment(&dropped_network);
        }
        if (end-last_log >= 1000000) {
            last_log=end;
            float rms_mean = 0;
            for (int bin = 0; bin < 10; bin++) rms_mean += item.features[bin * 15] / 10;
            uint32_t da,df,dn;
            portENTER_CRITICAL(&stats_mux); da=dropped_audio; df=dropped_features; dn=dropped_network; portEXIT_CRITICAL(&stats_mux);
            ESP_LOGI(TAG, "{\"score\":%.4f,\"rms_mean\":%.6f,\"capture_us\":%"PRId64",\"features_us\":%"PRId64",\"inference_us\":%"PRId64",\"decision_us\":%"PRId64",\"drop_audio\":%"PRIu32",\"drop_features\":%"PRIu32",\"drop_network\":%"PRIu32"}",
                     score,rms_mean,item.capture_us,item.features_us,end-start,end-item.completed_us,da,df,dn);
        }
    }
}

static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg; (void)data;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) esp_wifi_connect();
    else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(network, WIFI_READY); esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) xEventGroupSetBits(network, WIFI_READY);
}

static void network_task(void *arg) {
    (void)arg;
    wake_item_t item;
    char authorization[256], body[512];
    int length=snprintf(authorization,sizeof(authorization),"Bearer %s",CONFIG_FERRIS_DEVICE_TOKEN);
    bool configured = strlen(CONFIG_FERRIS_DEVICE_TOKEN)>0 && length>0 && length<(int)sizeof(authorization);
    while (1) {
        if (xQueueReceive(wake_queue,&item,portMAX_DELAY) != pdTRUE) continue;
        if (!accepts_generation(item.generation)) continue;
        if (!configured || !(xEventGroupGetBits(network)&WIFI_READY)) { increment(&dropped_network); continue; }
        snprintf(body,sizeof(body),"{\"device\":\"esp32-ferris\",\"confidence\":%.6f,\"metrics\":{\"capture_us\":%"PRId64",\"features_us\":%"PRId64",\"inference_us\":%"PRId64",\"decision_us\":%"PRId64"}}",
                 item.confidence,item.capture_us,item.features_us,item.inference_us,item.decision_us);
        esp_http_client_config_t config={.url=CONFIG_FERRIS_BRIDGE_URL,.timeout_ms=2000,.disable_auto_redirect=true};
        esp_http_client_handle_t client=esp_http_client_init(&config);
        if (!client) { increment(&dropped_network); continue; }
        esp_http_client_set_method(client,HTTP_METHOD_POST);
        esp_http_client_set_header(client,"Content-Type","application/json");
        esp_http_client_set_header(client,"Authorization",authorization);
        esp_http_client_set_post_field(client,body,strlen(body));
        if (!accepts_generation(item.generation)) { esp_http_client_cleanup(client); continue; }
        esp_err_t result=esp_http_client_perform(client);
        int status=esp_http_client_get_status_code(client);
        if (result!=ESP_OK || status<200 || status>=300) increment(&dropped_network);
        esp_http_client_cleanup(client);
        // No retries of stale wake events; they must not trigger greetings later.
    }
}

void app_main(void) {
    ESP_LOGI(TAG,"Ferris model ready: %d",FERRIS_MODEL_READY);
    if (!FERRIS_MODEL_READY) ESP_LOGW(TAG,"No trained model: capture/metrics only. Run tools/train_wake.py first.");
    esp_err_t err=nvs_flash_init();
    if (err==ESP_ERR_NVS_NO_FREE_PAGES || err==ESP_ERR_NVS_NEW_VERSION_FOUND) { ESP_ERROR_CHECK(nvs_flash_erase()); err=nvs_flash_init(); }
    ESP_ERROR_CHECK(err);
    audio_ring=xRingbufferCreate(8*sizeof(audio_chunk_t),RINGBUF_TYPE_NOSPLIT);
    feature_queue=xQueueCreate(3,sizeof(feature_item_t)); wake_queue=xQueueCreate(4,sizeof(wake_item_t));
    network=xEventGroupCreate(); configASSERT(audio_ring && feature_queue && wake_queue && network);
    const int pins[] = {CONFIG_FERRIS_BCLK, CONFIG_FERRIS_WS, CONFIG_FERRIS_DIN,
                       CONFIG_FERRIS_LED_RED, CONFIG_FERRIS_LED_GREEN, CONFIG_FERRIS_MUTE_BUTTON};
    for (unsigned i = 0; i < sizeof(pins)/sizeof(pins[0]); i++) {
        configASSERT(GPIO_IS_VALID_OUTPUT_GPIO(pins[i]));
        for (unsigned j = 0; j < i; j++) configASSERT(pins[i] != pins[j]);
    }
    ESP_LOGI(TAG, "Pins: SD=%d SCK=%d WS=%d red=%d green=%d mute=%d",
             CONFIG_FERRIS_DIN, CONFIG_FERRIS_BCLK, CONFIG_FERRIS_WS,
             CONFIG_FERRIS_LED_RED, CONFIG_FERRIS_LED_GREEN, CONFIG_FERRIS_MUTE_BUTTON);
    ferris_listen_init(&listen_state);
    gpio_config_t led={.pin_bit_mask=(1ULL<<CONFIG_FERRIS_LED_RED)|(1ULL<<CONFIG_FERRIS_LED_GREEN),.mode=GPIO_MODE_OUTPUT};
    ESP_ERROR_CHECK(gpio_config(&led));
    ESP_ERROR_CHECK(gpio_set_level(CONFIG_FERRIS_LED_RED, 1));
    ESP_ERROR_CHECK(gpio_set_level(CONFIG_FERRIS_LED_GREEN, 0));
    gpio_config_t button={.pin_bit_mask=1ULL<<CONFIG_FERRIS_MUTE_BUTTON,.mode=GPIO_MODE_INPUT,
                          .pull_up_en=GPIO_PULLUP_ENABLE,.pull_down_en=GPIO_PULLDOWN_DISABLE};
    ESP_ERROR_CHECK(gpio_config(&button));
    i2s_chan_config_t chan=I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0,I2S_ROLE_MASTER);
    chan.dma_desc_num=8; chan.dma_frame_num=256;
    ESP_ERROR_CHECK(i2s_new_channel(&chan,NULL,&rx));
    i2s_std_config_t mic={
        .clk_cfg=I2S_STD_CLK_DEFAULT_CONFIG(16000),
        .slot_cfg=I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,I2S_SLOT_MODE_MONO),
        .gpio_cfg={.mclk=I2S_GPIO_UNUSED,.bclk=CONFIG_FERRIS_BCLK,.ws=CONFIG_FERRIS_WS,.dout=I2S_GPIO_UNUSED,.din=CONFIG_FERRIS_DIN},
    };
    mic.slot_cfg.slot_mask=I2S_STD_SLOT_LEFT; // INMP441 L/R wired to GND.
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(rx,&mic));
    ESP_ERROR_CHECK(esp_netif_init()); ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta(); wifi_init_config_t wifi=WIFI_INIT_CONFIG_DEFAULT(); ESP_ERROR_CHECK(esp_wifi_init(&wifi));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,ESP_EVENT_ANY_ID,wifi_event,NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT,IP_EVENT_STA_GOT_IP,wifi_event,NULL));
    wifi_config_t config={0};
    strlcpy((char *)config.sta.ssid,CONFIG_FERRIS_WIFI_SSID,sizeof(config.sta.ssid));
    strlcpy((char *)config.sta.password,CONFIG_FERRIS_WIFI_PASSWORD,sizeof(config.sta.password));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA)); ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA,&config));
    if (strlen(CONFIG_FERRIS_WIFI_SSID)>0) ESP_ERROR_CHECK(esp_wifi_start());
    configASSERT(xTaskCreate(capture_task,"capture",6144,NULL,5,NULL)==pdPASS);
    configASSERT(xTaskCreate(controls_task,"controls",3072,NULL,4,NULL)==pdPASS);
    configASSERT(xTaskCreate(feature_task,"features",12288,NULL,3,NULL)==pdPASS);
    configASSERT(xTaskCreate(detect_task,"detect",4096,NULL,2,NULL)==pdPASS);
    configASSERT(xTaskCreate(network_task,"network",6144,NULL,1,NULL)==pdPASS);
}
