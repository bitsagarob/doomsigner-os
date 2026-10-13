#include "ss_display.h"

#include <fcntl.h>
#include <linux/spi/spidev.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

#include "ss_gpio.h"
#include <ss_panel_config.h>
#include "ss_video.h"

#define SPI_SPEED_HZ 40000000

/*
 * spidev's default per-transfer buffer is 4096 bytes, so a full 115200 byte
 * frame has to be chunked. SeedSigner's Python gets this for free from
 * spidev.writebytes2.
 */
#define SPI_CHUNK 4096

static int spi_fd = -1;
static int out_fd = -1; /* set when using the fd backend */

static void sleep_ms(long ms)
{
    struct timespec ts = { ms / 1000, (ms % 1000) * 1000000L };
    nanosleep(&ts, NULL);
}

static int spi_write(const uint8_t *bytes, size_t length)
{
    while (length > 0) {
        const size_t chunk = length < SPI_CHUNK ? length : SPI_CHUNK;
        struct spi_ioc_transfer transfer;

        memset(&transfer, 0, sizeof(transfer));
        transfer.tx_buf = (unsigned long)bytes;
        transfer.len = (uint32_t)chunk;
        transfer.speed_hz = SPI_SPEED_HZ;
        transfer.bits_per_word = 8;

        if (ioctl(spi_fd, SPI_IOC_MESSAGE(1), &transfer) < 0) {
            return -1;
        }

        bytes += chunk;
        length -= chunk;
    }

    return 0;
}

static int write_command(uint8_t command, const uint8_t *data, size_t length)
{
    ss_gpio_write(SS_PIN_DC, 0);
    if (spi_write(&command, 1) < 0) {
        return -1;
    }

    if (length > 0) {
        ss_gpio_write(SS_PIN_DC, 1);
        if (spi_write(data, length) < 0) {
            return -1;
        }
    }

    return 0;
}

static void hard_reset(void)
{
    ss_gpio_write(SS_PIN_RST, 1);
    sleep_ms(10);
    ss_gpio_write(SS_PIN_RST, 0);
    sleep_ms(10);
    ss_gpio_write(SS_PIN_RST, 1);
    sleep_ms(10);
}

static int set_window(void)
{
    /* Coordinates are 16 bit. A 320 wide panel does not fit in the low byte,
       which is easy to miss when the only panel you have tried is 240 wide. */
    const uint8_t columns[4] = {
        0x00, 0x00, (uint8_t)((SS_PANEL_W - 1) >> 8), (uint8_t)((SS_PANEL_W - 1) & 0xFF),
    };
    const uint8_t rows[4] = {
        0x00, 0x00, (uint8_t)((SS_PANEL_H - 1) >> 8), (uint8_t)((SS_PANEL_H - 1) & 0xFF),
    };

    if (write_command(0x2A, columns, 4) < 0) return -1;
    if (write_command(0x2B, rows, 4) < 0) return -1;
    if (write_command(0x2C, NULL, 0) < 0) return -1;

    return 0;
}

static int init_spi(void)
{
    if (ss_gpio_open() < 0) {
        fprintf(stderr, "doom: cannot open /dev/gpiochip0\n");
        return -1;
    }

    if (ss_gpio_output(SS_PIN_DC) < 0 || ss_gpio_output(SS_PIN_RST) < 0 ||
        ss_gpio_output(SS_PIN_BL) < 0) {
        fprintf(stderr, "doom: cannot claim the display control lines\n");
        return -1;
    }
    ss_gpio_write(SS_PIN_BL, 1);

    spi_fd = open(SS_SPI_DEVICE, O_RDWR);
    if (spi_fd < 0) {
        fprintf(stderr, "doom: cannot open %s\n", SS_SPI_DEVICE);
        return -1;
    }

    const uint8_t mode = SPI_MODE_0;
    const uint8_t bits = 8;
    const uint32_t speed = SPI_SPEED_HZ;
    ioctl(spi_fd, SPI_IOC_WR_MODE, &mode);
    ioctl(spi_fd, SPI_IOC_WR_BITS_PER_WORD, &bits);
    ioctl(spi_fd, SPI_IOC_WR_MAX_SPEED_HZ, &speed);

    hard_reset();

    /* Some drivers run the sequence more than once. Theirs says "yes, twice,
       once is not always enough", so match that rather than second-guess it. */
    for (int pass = 0; pass < SS_PANEL_INIT_REPEAT; pass++) {
        for (size_t i = 0; i < SS_PANEL_INIT_LEN; i++) {
            const ss_panel_cmd_t *entry = &SS_PANEL_INIT[i];
            if (write_command(entry->command, entry->data, entry->length) < 0) {
                fprintf(stderr, "doom: display init failed at command 0x%02X\n", entry->command);
                return -1;
            }
            if (entry->delay_ms) {
                sleep_ms(entry->delay_ms);
            }
        }
    }

#if SS_PANEL_MADCTL
    /* Rotate on the controller, not in software: the panel is natively portrait
       and this costs nothing per frame. */
    const uint8_t madctl = SS_PANEL_MADCTL;
    if (write_command(0x36, &madctl, 1) < 0) {
        fprintf(stderr, "doom: could not set display rotation\n");
        return -1;
    }
#endif

    sleep_ms(120); /* SLPOUT needs time before the first frame */
    return 0;
}

int ss_display_init(void)
{
    const char *backend = getenv("SS_DISPLAY");

    if (backend && strcmp(backend, "fd") == 0) {
        const char *fd_text = getenv("SS_DISPLAY_FD");
        out_fd = fd_text ? atoi(fd_text) : STDOUT_FILENO;
        return 0;
    }

    return init_spi();
}

void ss_display_push(const uint16_t *frame)
{
    static uint8_t wire[SS_PANEL_W * SS_PANEL_H * 2];
    const size_t bytes = sizeof(wire);

    ss_pack_wire(frame, wire);

    if (out_fd >= 0) {
        const uint8_t *cursor = wire;
        size_t remaining = bytes;
        while (remaining > 0) {
            const ssize_t written = write(out_fd, cursor, remaining);
            if (written <= 0) {
                return;
            }
            cursor += written;
            remaining -= (size_t)written;
        }
        return;
    }

    if (set_window() < 0) {
        return;
    }

    ss_gpio_write(SS_PIN_DC, 1);
    spi_write(wire, bytes);
}

void ss_display_close(void)
{
    if (spi_fd >= 0) {
        close(spi_fd);
        spi_fd = -1;
        ss_gpio_close();
    }
}
