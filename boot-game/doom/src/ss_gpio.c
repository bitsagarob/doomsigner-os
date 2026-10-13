#include "ss_gpio.h"

#include <fcntl.h>
#include <linux/gpio.h>
#include <stdint.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

/*
 * The line offsets on the Pi's gpiochip0 are the BCM numbers, which is what
 * SeedSigner's io_config.json names for every pin.
 */
#define SS_GPIO_CHIP "/dev/gpiochip0"
#define SS_GPIO_LINES 64

static int chip_fd = -1;
static int line_fd[SS_GPIO_LINES];

int ss_gpio_open(void)
{
    /* The display and the buttons both open it. */
    if (chip_fd >= 0) {
        return 0;
    }

    chip_fd = open(SS_GPIO_CHIP, O_RDWR | O_CLOEXEC);
    if (chip_fd < 0) {
        return -1;
    }

    for (int i = 0; i < SS_GPIO_LINES; i++) {
        line_fd[i] = -1;
    }
    return 0;
}

void ss_gpio_close(void)
{
    if (chip_fd < 0) {
        return;
    }

    for (int i = 0; i < SS_GPIO_LINES; i++) {
        if (line_fd[i] >= 0) {
            close(line_fd[i]);
            line_fd[i] = -1;
        }
    }
    close(chip_fd);
    chip_fd = -1;
}

static int request_line(int pin, uint64_t flags)
{
    if (chip_fd < 0 || pin < 0 || pin >= SS_GPIO_LINES) {
        return -1;
    }

    struct gpio_v2_line_request request;
    memset(&request, 0, sizeof(request));
    request.offsets[0] = (uint32_t)pin;
    request.num_lines = 1;
    request.config.flags = flags;
    strncpy(request.consumer, "doom", sizeof(request.consumer) - 1);

    if (ioctl(chip_fd, GPIO_V2_GET_LINE_IOCTL, &request) < 0) {
        return -1;
    }

    /* The kernel opens the line fd close-on-exec, so the wallet gets the pins. */
    line_fd[pin] = request.fd;
    return 0;
}

int ss_gpio_output(int pin)
{
    return request_line(pin, GPIO_V2_LINE_FLAG_OUTPUT);
}

int ss_gpio_input_pullup(int pin)
{
    return request_line(pin, GPIO_V2_LINE_FLAG_INPUT | GPIO_V2_LINE_FLAG_BIAS_PULL_UP);
}

void ss_gpio_write(int pin, int high)
{
    struct gpio_v2_line_values values = { .bits = high ? 1 : 0, .mask = 1 };
    ioctl(line_fd[pin], GPIO_V2_LINE_SET_VALUES_IOCTL, &values);
}

int ss_gpio_read(int pin)
{
    struct gpio_v2_line_values values = { .bits = 0, .mask = 1 };

    /* Buttons idle high, so a line that cannot be read reads as not pressed. */
    if (ioctl(line_fd[pin], GPIO_V2_LINE_GET_VALUES_IOCTL, &values) < 0) {
        return 1;
    }
    return (int)(values.bits & 1);
}
