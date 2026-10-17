#!/bin/sh
# Boot: ./run.sh /pytest-init.sh
# Brings up a glibc CPython 3.12 from hostfs (/pyfs) plus the SeedSigner app source
# (/app), then imports the app and drives the real kernel SPI bus through periphery.
mount -t proc proc /proc 2>/dev/null
mount -t sysfs sysfs /sys 2>/dev/null
mount -t devtmpfs devtmpfs /dev 2>/dev/null
mount -t tmpfs tmpfs /tmp 2>/dev/null
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
export HOME=/tmp

mkdir -p /pyfs /app /out 2>/dev/null
mount -t hostfs -o /home/rob/apps/_scratch/uml/pyfs   none /pyfs 2>&1
mount -t hostfs -o /home/rob/apps/_scratch/uml/appsrc-boot none /app  2>&1
mount -t hostfs -o /home/rob/apps/_scratch/uml/out    none /out  2>&1

echo "=== load modules ==="
insmod /mods/spidev.ko; echo "spidev rc=$?"
insmod /mods/ss_display_capture.ko; echo "capture rc=$?"
ls -la /dev/spidev0.0 /dev/ss_spicap /dev/gpiochip0 2>&1

cat /dev/ss_spicap > /out/bootgame-fixed.bin &
sleep 1
cd /app && timeout 240 python3 /app/shoot_boot_game.py 2>&1 | grep -vE 'font load|Supersampl'
sleep 1; sync
echo o > /proc/sysrq-trigger
sleep 3
