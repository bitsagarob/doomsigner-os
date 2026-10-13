import pytest

from bootgame.catalog import DOOM, DOOM_BINARY, DOOM_WAD, SNAKE, Game, available_games


def test_only_builtin_games_when_nothing_external_is_installed():
    assert available_games(exists=lambda path: False) == [SNAKE]


def test_an_external_game_appears_once_its_binary_is_present():
    games = available_games(exists=lambda path: path == DOOM_BINARY)

    # First, because the chooser opens on the first entry.
    assert [game.name for game in games] == ["DOOM", "SNAKE"]


def test_a_builtin_game_names_a_module_and_no_binary():
    assert SNAKE.module == "bootgame.games.snake"
    assert SNAKE.binary is None
    assert not SNAKE.is_external


def test_an_external_game_names_a_binary_and_no_module():
    assert DOOM.binary == DOOM_BINARY
    assert DOOM.module is None
    assert DOOM.is_external


def test_doom_is_launched_with_its_wad(monkeypatch):
    # doomgeneric searches only its working directory, which is /opt/src by
    # then. Launched bare, it exits at once and start.sh runs the wallet.
    from bootgame import launch

    seen = []
    monkeypatch.setattr(launch.os, "execv", lambda path, argv: seen.append((path, argv)))

    launch.launch_external(DOOM)

    assert seen == [(DOOM_BINARY, [DOOM_BINARY, "-iwad", DOOM_WAD])]


def test_the_wad_path_is_where_the_package_installs_it():
    from pathlib import Path

    mk = Path(__file__).parents[2] / "opt/external-packages/doomgeneric/doomgeneric.mk"
    install = mk.read_text().split("define DOOMGENERIC_INSTALL_TARGET_CMDS")[1]

    assert "$(TARGET_DIR)" + DOOM_BINARY in install
    assert "$(TARGET_DIR)" + DOOM_WAD in install


def test_a_game_must_be_one_or_the_other():
    with pytest.raises(ValueError):
        Game("BOTH", module="x", binary="/y")

    with pytest.raises(ValueError):
        Game("NEITHER")


def test_every_registered_builtin_module_actually_imports():
    # Games are imported lazily, so a typo in the catalog would otherwise only
    # surface on the device, at the moment someone picks that game.
    import importlib

    for game in (SNAKE,):
        assert hasattr(importlib.import_module(game.module), "play")
