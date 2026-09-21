#include "ferris_controls.h"

void ferris_listen_init(ferris_listen_state_t *state) {
    *state = (ferris_listen_state_t){.generation = 1};
}

void ferris_listen_toggle(ferris_listen_state_t *state) {
    state->muted = !state->muted;
    state->ready = false;
    state->generation++;
    state->alert_until_us = 0;
}

bool ferris_listen_accepts(const ferris_listen_state_t *state, uint32_t generation) {
    return !state->muted && state->ready && state->generation == generation;
}

bool ferris_listen_ready(ferris_listen_state_t *state, uint32_t generation) {
    if (state->muted || state->generation != generation) return false;
    state->ready = true;
    return true;
}

bool ferris_listen_alert(ferris_listen_state_t *state, uint32_t generation, int64_t now_us) {
    if (!ferris_listen_accepts(state, generation)) return false;
    state->alert_until_us = now_us + 300000;
    return true;
}

/* A extracao publica uma janela a cada 5 blocos de audio; um salto diferente
   disso significa bloco perdido, e duas janelas descontinuas nao confirmam. */
#define FERRIS_WAKE_STEP 5
#define FERRIS_WAKE_QUIET_US 3000000

void ferris_wake_init(ferris_wake_t *wake) {
    *wake = (ferris_wake_t){.phase = FERRIS_WAKE_LISTENING};
}

void ferris_wake_reset(ferris_wake_t *wake) {
    wake->phase = FERRIS_WAKE_LISTENING;
    wake->last_sequence = 0;
    wake->quiet_until_us = 0;
}

bool ferris_wake_update(ferris_wake_t *wake, bool positive, uint32_t generation,
                        uint32_t sequence, int64_t now_us) {
    if (wake->generation != generation) {
        ferris_wake_reset(wake);
        wake->generation = generation;
    } else if (wake->last_sequence && sequence != wake->last_sequence + FERRIS_WAKE_STEP) {
        wake->phase = FERRIS_WAKE_LISTENING;
    }
    wake->last_sequence = sequence;
    if (!positive) {
        wake->phase = FERRIS_WAKE_LISTENING;
        return false;
    }
    if (wake->phase == FERRIS_WAKE_LISTENING) {
        wake->phase = FERRIS_WAKE_CONFIRMING;
        return false;
    }
    /* Confirmada. O silencio seguinte evita repetir o alerta na mesma fala;
       ate la a tarefa segue confirmada, sem precisar de uma dupla nova. */
    if (now_us < wake->quiet_until_us) return false;
    wake->quiet_until_us = now_us + FERRIS_WAKE_QUIET_US;
    wake->phase = FERRIS_WAKE_LISTENING;
    return true;
}

void ferris_button_init(ferris_button_t *button, bool pressed, int64_t now_us) {
    *button = (ferris_button_t){.candidate = pressed, .stable = pressed,
                              .armed = !pressed, .changed_us = now_us};
}

bool ferris_button_update(ferris_button_t *button, bool pressed, int64_t now_us) {
    if (pressed != button->candidate) {
        button->candidate = pressed;
        button->changed_us = now_us;
    }
    if (button->stable == button->candidate || now_us - button->changed_us < 30000) return false;
    button->stable = button->candidate;
    if (!button->stable) { button->armed = true; return false; }
    if (!button->armed) return false;
    button->armed = false;
    return true;
}
