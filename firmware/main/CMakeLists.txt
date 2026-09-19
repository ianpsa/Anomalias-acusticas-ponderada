idf_component_register(SRCS "main.c" INCLUDE_DIRS "."
    REQUIRES ferris_dsp ferris_controls esp_driver_i2s esp_driver_gpio esp_timer esp_wifi esp_netif esp_event nvs_flash esp_http_client esp_ringbuf mbedtls)
