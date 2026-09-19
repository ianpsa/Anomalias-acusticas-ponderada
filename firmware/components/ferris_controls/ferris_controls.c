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
