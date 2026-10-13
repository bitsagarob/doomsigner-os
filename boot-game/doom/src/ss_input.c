#include "ss_input.h"

#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "doomkeys.h"
#include "ss_gpio.h"
#include <ss_panel_config.h>

typedef struct {
    int pin;
    int doom_key;
    int side_button; /* SS_KEY1/2/3, or 0 */
} button_t;

/*
 * Eight buttons for a game that expects a keyboard, so the side buttons double
 * up: they drive DOOM and feed the unlock detector. A stray press is harmless
 * because only the exact sequence unlocks.
 */
static const button_t BUTTONS[] = {
    { SS_PIN_UP,    KEY_UPARROW,    0 },
    { SS_PIN_DOWN,  KEY_DOWNARROW,  0 },
    { SS_PIN_LEFT,  KEY_LEFTARROW,  0 },
    { SS_PIN_RIGHT, KEY_RIGHTARROW, 0 },
    { SS_PIN_PRESS, KEY_FIRE,       0 },
    { SS_PIN_KEY1,  KEY_USE,        SS_KEY1 },
    { SS_PIN_KEY2,  KEY_ENTER,      SS_KEY2 },
    { SS_PIN_KEY3,  KEY_ESCAPE,     SS_KEY3 },
};

#define BUTTON_COUNT (sizeof(BUTTONS) / sizeof(BUTTONS[0]))

static int held[BUTTON_COUNT];
static int use_stdin = 0;

static int init_stdin(void)
{
    const int flags = fcntl(STDIN_FILENO, F_GETFL, 0);
    fcntl(STDIN_FILENO, F_SETFL, flags | O_NONBLOCK);
    use_stdin = 1;
    return 0;
}

int ss_input_init(void)
{
    const char *backend = getenv("SS_INPUT");

    memset(held, 0, sizeof(held));

    if (backend && strcmp(backend, "stdin") == 0) {
        return init_stdin();
    }

    /* ss_display_init has usually opened the chip already; that is fine. */
    if (ss_gpio_open() < 0) {
        fprintf(stderr, "doom: cannot open /dev/gpiochip0 for buttons\n");
        return -1;
    }

    for (size_t i = 0; i < BUTTON_COUNT; i++) {
        if (ss_gpio_input_pullup(BUTTONS[i].pin) < 0) {
            fprintf(stderr, "doom: cannot claim button line %d\n", BUTTONS[i].pin);
            return -1;
        }
    }

    return 0;
}

static int poll_stdin(int *doom_key, int *pressed, int *side_button)
{
    char c;
    if (read(STDIN_FILENO, &c, 1) != 1) {
        return 0;
    }

    size_t index;
    switch (c) {
        case 'w': index = 0; break;
        case 's': index = 1; break;
        case 'a': index = 2; break;
        case 'd': index = 3; break;
        case ' ': index = 4; break;
        case '1': index = 5; break;
        case '2': index = 6; break;
        case '3': index = 7; break;
        default: return 0;
    }

    *doom_key = BUTTONS[index].doom_key;
    *side_button = BUTTONS[index].side_button;
    *pressed = 1;
    return 1;
}

int ss_input_poll(int *doom_key, int *pressed, int *side_button)
{
    if (use_stdin) {
        return poll_stdin(doom_key, pressed, side_button);
    }

    for (size_t i = 0; i < BUTTON_COUNT; i++) {
        /* Pull-ups, so a pressed button reads low. */
        const int down = ss_gpio_read(BUTTONS[i].pin) == 0;

        if (down == held[i]) {
            continue;
        }

        held[i] = down;
        *doom_key = BUTTONS[i].doom_key;
        *pressed = down;
        *side_button = down ? BUTTONS[i].side_button : 0;
        return 1;
    }

    return 0;
}
