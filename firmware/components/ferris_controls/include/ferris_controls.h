#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Call shared-state functions under the application's short critical section. */
typedef struct {
    bool muted;
    bool ready;
    uint32_t generation;
    int64_t alert_until_us;
} ferris_listen_state_t;

typedef struct {
    bool candidate;
    bool stable;
    bool armed;
    int64_t changed_us;
} ferris_button_t;

/* Ciclo da palavra de ativacao. O contador de acertos, o carimbo de cooldown e a
   checagem de continuidade viram um estado com nome e uma transicao por janela. */
typedef enum {
    FERRIS_WAKE_LISTENING,  /* esperando a primeira janela positiva */
    FERRIS_WAKE_CONFIRMING, /* uma positiva vista; a proxima confirma */
} ferris_wake_phase_t;

typedef struct {
    ferris_wake_phase_t phase;
    uint32_t generation;
    uint32_t last_sequence;
    int64_t quiet_until_us;
} ferris_wake_t;

void ferris_listen_init(ferris_listen_state_t *state);
void ferris_listen_toggle(ferris_listen_state_t *state);
bool ferris_listen_accepts(const ferris_listen_state_t *state, uint32_t generation);
bool ferris_listen_ready(ferris_listen_state_t *state, uint32_t generation);
bool ferris_listen_alert(ferris_listen_state_t *state, uint32_t generation, int64_t now_us);
void ferris_wake_init(ferris_wake_t *wake);
void ferris_wake_reset(ferris_wake_t *wake);
/* Devolve true exatamente na janela que confirma a palavra. */
bool ferris_wake_update(ferris_wake_t *wake, bool positive, uint32_t generation,
                        uint32_t sequence, int64_t now_us);
void ferris_button_init(ferris_button_t *button, bool pressed, int64_t now_us);
bool ferris_button_update(ferris_button_t *button, bool pressed, int64_t now_us);
