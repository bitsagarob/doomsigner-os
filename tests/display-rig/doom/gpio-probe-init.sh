#!/bin/sh
# Boot: ./run.sh /gpio-probe-init.sh
# Static probe: DOOM's ss_gpio.c reading KEY1..3 while presses are injected.
mount -t proc proc /proc 2>/dev/null
mount -t sysfs sysfs /sys 2>/dev/null
mount -t devtmpfs devtmpfs /dev 2>/dev/null
mount -t tmpfs tmpfs /tmp 2>/dev/null
mount -t hostfs -o /home/rob/apps/_scratch/uml/doom-stage/probe none /rig
insmod /mods/spidev.ko
insmod /mods/ss_display_capture.ko
/rig/gpioprobe &
sleep 1
for pair in "21 KEY1" "20 KEY2" "16 KEY3"; do
  set -- $pair
  printf "\\$(printf %03o $1)\\000" > /dev/ss_spicap; echo "inject $2 down"
  sleep 0.6
  printf "\\$(printf %03o $1)\\001" > /dev/ss_spicap; echo "inject $2 up"
  sleep 0.5
done
sleep 1
echo o > /proc/sysrq-trigger
sleep 3
