/*
 * GPIO through /dev/gpiochip0 and the kernel's v2 line API, the same path
 * SeedSigner's Python takes to the same pins (python-periphery).
 *
 * The first version mapped the BCM2835 registers through /dev/gpiomem.
 * SeedSigner OS builds no gpiomem driver, so on the device that open failed
 * and DOOM handed straight to the wallet; the Virtual HAT, which provides a
 * gpiochip and no registers, could not run it either.
 *
 * Pin numbers here are BCM, which are the line offsets on gpiochip0. They
 * come from ss_panel_config.h, generated from SeedSigner's io_config.json.
 */
#ifndef SS_GPIO_H
#define SS_GPIO_H

int ss_gpio_open(void);
void ss_gpio_close(void);

int ss_gpio_output(int pin);
int ss_gpio_input_pullup(int pin);
void ss_gpio_write(int pin, int high);
int ss_gpio_read(int pin);

#endif /* SS_GPIO_H */
