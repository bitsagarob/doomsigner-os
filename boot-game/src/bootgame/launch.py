"""
Handing this process over to something else.

Always `os.execv`, never fork: the process image is replaced outright, so
nothing of ours stays resident once the wallet is running.
"""

import logging
import os

logger = logging.getLogger(__name__)

SEEDSIGNER_SRC = "/opt/src"
PYTHON = "/usr/bin/python3"


def launch_seedsigner() -> None:
    """Replace this process with SeedSigner. Does not return."""
    logger.info("handing off to SeedSigner")
    os.chdir(SEEDSIGNER_SRC)
    os.execv(PYTHON, [PYTHON, "main.py"])


def launch_external(game) -> None:
    """
    Replace this process with an external game. Does not return.

    The trade against fork is that quitting an external game ends the session
    rather than returning to the menu, which for a toy is the right way round.
    """
    logger.info("launching %s", game.name)
    os.execv(game.binary, [game.binary, *game.args])
