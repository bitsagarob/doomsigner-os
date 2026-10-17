#!/bin/sh
# Boot: ./run.sh /doom-chain-init.sh
# The whole boot chain on the Virtual HAT: start.sh's own launch line, the boot game,
# DOOM by execv, the unlock, and the wallet by execv. Staged from /home/rob/apps/_scratch/uml/doom-stage.
mount -t proc proc /proc 2>/dev/null
mount -t sysfs sysfs /sys 2>/dev/null
mount -t devtmpfs devtmpfs /dev 2>/dev/null
mount -t tmpfs tmpfs /tmp 2>/dev/null
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
export HOME=/tmp

# A private /etc carrying the SeedSigner OS marker, so the wallet takes its device
# code paths, without leaving the marker in the shared sandbox rootfs.
cp -a /etc /tmp/etc && mount -t tmpfs tmpfs /etc && cp -a /tmp/etc/. /etc/ && touch /etc/seedsigner-os-release

mkdir -p /pyfs /out /opt/src /usr/local/games /usr/local/bootgame /rigshim /rig 2>/dev/null
mount -t hostfs -o /home/rob/apps/_scratch/uml/pyfs none /pyfs
mount -t hostfs -o /home/rob/apps/_scratch/uml/out none /out
mount -t hostfs -o /home/rob/apps/_scratch/uml/doom-stage/opt-src none /opt/src
mount -t hostfs -o /home/rob/apps/_scratch/uml/doom-stage/games-plain none /usr/local/games
mount -t hostfs -o /home/rob/apps/_scratch/uml/doom-stage/bootgame-pkg none /usr/local/bootgame
mount -t hostfs -o /home/rob/apps/_scratch/uml/doom-stage/shim none /rigshim
mount -t hostfs -o /home/rob/apps/_scratch/uml/doom-stage/rig none /rig

insmod /mods/spidev.ko; echo "spidev rc=$?"
insmod /mods/ss_display_capture.ko; echo "capture rc=$?"
ls -la /dev/spidev0.0 /dev/ss_spicap /dev/gpiochip0 2>&1

cat /dev/ss_spicap > /out/doom-plain.bin &
sleep 1

# start.sh, verbatim apart from the log redirect
cd /opt/src/
{
    PYTHONPATH=/usr/local/bootgame /usr/bin/python3 -m bootgame.boot ||
        /usr/bin/python3 main.py
} > /out/doom-plain-boot.log 2>&1 &

timeout 300 /usr/bin/python3 /rig/fingers.py
sleep 1; sync
echo o > /proc/sysrq-trigger
sleep 3
