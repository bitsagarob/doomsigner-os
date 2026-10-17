/* Scratch probe: does DOOM's ss_gpio.c see presses injected through /dev/ss_spicap? */
#include <stdio.h>
#include <time.h>
#include "ss_gpio.h"
int main(void) {
    int pins[3] = {21, 20, 16};
    if (ss_gpio_open() < 0) { perror("open"); return 1; }
    for (int i = 0; i < 3; i++)
        if (ss_gpio_input_pullup(pins[i]) < 0) { fprintf(stderr, "claim %d failed\n", pins[i]); perror("claim"); return 1; }
    int last[3] = {-1, -1, -1};
    for (int n = 0; n < 200; n++) {           /* 10 s at 50 ms */
        for (int i = 0; i < 3; i++) {
            int v = ss_gpio_read(pins[i]);
            if (v != last[i]) { printf("t=%d.%02ds pin %d -> %d\n", n / 20, (n % 20) * 5, pins[i], v); fflush(stdout); last[i] = v; }
        }
        struct timespec ts = {0, 50000000}; nanosleep(&ts, NULL);
    }
    return 0;
}
