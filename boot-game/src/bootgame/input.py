"""
Maps SeedSigner's GPIO channel numbers onto `Key`.

This is the only module in the package that imports SeedSigner, and so the only
one that needs a Raspberry Pi or the emulator in order to import at all.
"""

import logging
from typing import Dict, Optional

from seedsigner.hardware.buttons import HardwareButtons, HardwareButtonsConstants

from bootgame.edges import EdgeDetector
from bootgame.keys import Key

logger = logging.getLogger(__name__)

CHANNEL_TO_KEY: Dict[int, Key] = {
    HardwareButtonsConstants.KEY_UP: Key.UP,
    HardwareButtonsConstants.KEY_DOWN: Key.DOWN,
    HardwareButtonsConstants.KEY_LEFT: Key.LEFT,
    HardwareButtonsConstants.KEY_RIGHT: Key.RIGHT,
    HardwareButtonsConstants.KEY_PRESS: Key.PRESS,
    HardwareButtonsConstants.KEY1: Key.KEY1,
    HardwareButtonsConstants.KEY2: Key.KEY2,
    HardwareButtonsConstants.KEY3: Key.KEY3,
}


def key_for_channel(channel: int) -> Optional[Key]:
    return CHANNEL_TO_KEY.get(channel)


class ButtonReader:
    """Thin adapter: polls the real buttons, delegates the logic to EdgeDetector."""

    def __init__(self):
        self.buttons = HardwareButtons.get_instance()
        self.edges = EdgeDetector(HardwareButtonsConstants.ALL_KEYS, key_for_channel)


    def presses(self):
        # EdgeDetector needs the raw level. Since the app fork's 2026-08-09 debounce rework,
        # check_for_low() fires again every other poll while a key is held, so one held KEY2
        # reads as two presses and resets the unlock. 0.8.7 has no is_pressed() and its
        # check_for_low() is still a plain level read.
        is_pressed = getattr(self.buttons, "is_pressed", None)
        if is_pressed is None:
            return self.edges.presses(lambda channel: self.buttons.check_for_low(key=channel))
        return self.edges.presses(is_pressed)
