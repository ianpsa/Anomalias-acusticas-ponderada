#include <assert.h>
#include "ferris_controls.h"

int main(void) {
    ferris_button_t button;
    ferris_button_init(&button, false, 0);
    assert(!ferris_button_update(&button, true, 10000));
    assert(!ferris_button_update(&button, false, 15000));
    assert(!ferris_button_update(&button, true, 20000));
    assert(!ferris_button_update(&button, true, 49000));
    assert(ferris_button_update(&button, true, 50000));
    assert(!ferris_button_update(&button, true, 500000)); // Hold: exactly one toggle.
    assert(!ferris_button_update(&button, false, 510000));
    assert(!ferris_button_update(&button, false, 540000));
    assert(!ferris_button_update(&button, true, 550000));
    assert(ferris_button_update(&button, true, 580000));

    ferris_button_init(&button, true, 0); // Held during boot: wait for release.
    assert(!ferris_button_update(&button, true, 100000));
    assert(!ferris_button_update(&button, false, 110000));
    assert(!ferris_button_update(&button, false, 140000));
    assert(!ferris_button_update(&button, true, 150000));
    assert(ferris_button_update(&button, true, 180000));

    /* Maquina de estados da palavra: duas janelas seguidas confirmam. */
    ferris_wake_t wake;
    ferris_wake_init(&wake);
    assert(!ferris_wake_update(&wake, true, 1, 5, 1000));       /* primeira positiva */
    assert(ferris_wake_update(&wake, true, 1, 10, 2000));       /* segunda confirma */
    assert(!ferris_wake_update(&wake, true, 1, 15, 3000));      /* silencio de 3 s */
    assert(!ferris_wake_update(&wake, true, 1, 20, 4000));
    assert(ferris_wake_update(&wake, true, 1, 25, 3002000));    /* silencio vencido */

    ferris_wake_init(&wake);
    assert(!ferris_wake_update(&wake, true, 1, 5, 1000));
    assert(!ferris_wake_update(&wake, false, 1, 10, 2000));     /* negativa corta a dupla */
    assert(!ferris_wake_update(&wake, true, 1, 15, 3000));
    assert(ferris_wake_update(&wake, true, 1, 20, 4000));

    ferris_wake_init(&wake);
    assert(!ferris_wake_update(&wake, true, 1, 5, 1000));
    assert(!ferris_wake_update(&wake, true, 1, 30, 2000));      /* lacuna: recomeca */
    assert(ferris_wake_update(&wake, true, 1, 35, 3000));

    ferris_wake_init(&wake);
    assert(!ferris_wake_update(&wake, true, 1, 5, 1000));
    assert(!ferris_wake_update(&wake, true, 2, 10, 2000));      /* mute troca a geracao */
    assert(ferris_wake_update(&wake, true, 2, 15, 3000));

    ferris_listen_state_t state;
    ferris_listen_init(&state);
    const unsigned before = state.generation;
    assert(!ferris_listen_accepts(&state, before)); // DMA warmup not ready.
    assert(ferris_listen_ready(&state, before));
    assert(ferris_listen_alert(&state, before, 100));
    assert(state.alert_until_us == 300100);
    ferris_listen_toggle(&state);
    assert(state.muted && !state.ready && state.alert_until_us == 0);
    assert(!ferris_listen_ready(&state, before));
    assert(!ferris_listen_accepts(&state, before));
    assert(!ferris_listen_alert(&state, before, 200));
    const unsigned muted = state.generation;
    assert(!ferris_listen_ready(&state, muted));
    ferris_listen_toggle(&state);
    assert(!state.muted && !state.ready);
    assert(!ferris_listen_ready(&state, before)); // Stale capture cannot re-enable processing.
    assert(ferris_listen_ready(&state, state.generation));
    assert(!ferris_listen_accepts(&state, before)); // Queued audio from before mute is invalid.
    assert(!ferris_listen_accepts(&state, muted));
    assert(ferris_listen_accepts(&state, state.generation));
    return 0;
}
