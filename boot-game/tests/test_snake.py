from random import Random

import pytest

from bootgame.games.snake import SnakeGame
from bootgame.keys import Key


@pytest.fixture
def game():
    return SnakeGame(grid_width=10, grid_height=10, rng=Random(0))


def test_opens_with_a_length_three_snake_heading_right(game):
    assert len(game.snake) == 3
    assert game.direction == (1, 0)
    assert game.score == 0
    assert not game.game_over


def test_food_never_spawns_on_the_snake():
    # Many seeds, because a single one proves nothing about the exclusion.
    for seed in range(200):
        game = SnakeGame(grid_width=6, grid_height=6, rng=Random(seed))
        assert game.food not in game.snake


def test_tick_advances_the_head_and_keeps_the_length(game):
    game.food = None
    head_x, head_y = game.snake[0]

    game.tick()

    assert game.snake[0] == (head_x + 1, head_y)
    assert len(game.snake) == 3


def test_turn_changes_direction(game):
    game.turn(Key.UP)
    assert game.direction == (0, -1)


def test_turn_ignores_a_reversal_onto_its_own_neck(game):
    game.turn(Key.LEFT)
    assert game.direction == (1, 0)


def test_turn_ignores_non_directional_keys(game):
    game.turn(Key.KEY1)
    assert game.direction == (1, 0)


def test_running_into_a_wall_ends_the_game(game):
    game.food = None
    for _ in range(game.grid_width):
        game.tick()

    assert game.game_over


def test_a_finished_game_stops_advancing(game):
    game.game_over = True
    before = list(game.snake)

    game.tick()

    assert game.snake == before


def test_eating_food_grows_the_snake_and_scores(game):
    head_x, head_y = game.snake[0]
    game.food = (head_x + 1, head_y)

    game.tick()

    assert game.score == 1
    assert len(game.snake) == 4
    assert not game.game_over


def test_the_vacating_tail_cell_is_not_a_collision():
    # Curling onto the cell the tail is leaving this same tick is legal.
    game = SnakeGame(grid_width=10, grid_height=10, rng=Random(0))
    game.food = None
    game.snake = [(2, 2), (2, 1), (1, 1), (1, 2)]
    game.direction = (0, 1)

    game.tick()

    assert not game.game_over
    assert game.snake[0] == (2, 3)


def test_biting_its_own_body_ends_the_game():
    game = SnakeGame(grid_width=10, grid_height=10, rng=Random(0))
    game.food = None
    game.snake = [(2, 2), (3, 2), (3, 3), (2, 3), (1, 3), (1, 2)]
    # (2, 3) is mid-body, not the tail, so it is still occupied after this tick.
    game.direction = (0, 1)

    game.tick()

    assert game.game_over


def test_reset_restores_the_opening_position(game):
    game.tick()
    game.score = 7
    game.game_over = True

    game.reset()

    assert len(game.snake) == 3
    assert game.score == 0
    assert not game.game_over


def test_a_full_board_leaves_no_food():
    game = SnakeGame(grid_width=2, grid_height=2, rng=Random(0))
    game.snake = [(0, 0), (1, 0), (0, 1), (1, 1)]

    assert game._place_food() is None


# --- rendering -------------------------------------------------------------

CANVAS = 240


def test_render_fills_the_whole_canvas():
    from PIL import Image, ImageDraw

    from bootgame.games.snake import render

    canvas = Image.new("RGB", (CANVAS, CANVAS))
    render(ImageDraw.Draw(canvas), SnakeGame(rng=Random(0)), CANVAS, CANVAS)

    assert canvas.getbbox() is not None


def test_the_head_is_drawn_in_the_head_colour():
    from PIL import Image, ImageDraw

    from bootgame.games.snake import SNAKE_HEAD, render

    canvas = Image.new("RGB", (CANVAS, CANVAS))
    game = SnakeGame(grid_width=12, grid_height=12, rng=Random(0))
    game.food = None
    cell = CANVAS // 12

    render(ImageDraw.Draw(canvas), game, CANVAS, CANVAS)

    head_x, head_y = game.snake[0]
    assert canvas.getpixel((head_x * cell + cell // 2, head_y * cell + cell // 2)) == SNAKE_HEAD


def test_food_is_drawn_in_the_food_colour():
    from PIL import Image, ImageDraw

    from bootgame.games.snake import FOOD, render

    canvas = Image.new("RGB", (CANVAS, CANVAS))
    game = SnakeGame(grid_width=12, grid_height=12, rng=Random(0))
    cell = CANVAS // 12

    render(ImageDraw.Draw(canvas), game, CANVAS, CANVAS)

    food_x, food_y = game.food
    assert canvas.getpixel((food_x * cell + cell // 2, food_y * cell + cell // 2)) == FOOD


def test_it_renders_on_a_non_square_canvas():
    from PIL import Image, ImageDraw

    from bootgame.games.snake import render

    image = Image.new("RGB", (320, 240))
    render(ImageDraw.Draw(image), SnakeGame(rng=Random(0)), 320, 240)

    assert image.getbbox() is not None


# --- the play loop ---------------------------------------------------------


def test_the_board_fills_a_320x240_panel(monkeypatch):
    from PIL import Image, ImageDraw

    from bootgame.games import snake
    from bootgame.games.snake import SNAKE_BODY
    from bootgame.unlock import UnlockSequence
    from conftest import FakeReader, FakeRenderer

    monkeypatch.setattr(snake.time, "sleep", lambda seconds: None)
    renderer = FakeRenderer()
    renderer.canvas = Image.new("RGB", (320, 240))
    renderer.draw = ImageDraw.Draw(renderer.canvas)
    renderer.canvas_width = 320

    with pytest.raises(StopIteration):
        snake.play(renderer, FakeReader([[]]), UnlockSequence())

    # One tick in, the tail is in column 1 of the middle row. A 12x12 board
    # centred on this panel would leave that pixel in the black bar.
    assert renderer.canvas.getpixel((30, 6 * 20 + 10)) == SNAKE_BODY


def test_the_unlock_sequence_hands_off_mid_game(renderer, monkeypatch):
    from bootgame.games import snake
    from bootgame.unlock import UnlockSequence
    from conftest import FakeReader, HandedOff

    monkeypatch.setattr(snake.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        snake, "launch_seedsigner", lambda: (_ for _ in ()).throw(HandedOff())
    )
    reader = FakeReader([[Key.KEY1], [Key.KEY2], [Key.KEY3]])

    with pytest.raises(HandedOff):
        snake.play(renderer, reader, UnlockSequence())


def test_ordinary_play_never_hands_off(renderer, monkeypatch):
    from bootgame.games import snake
    from bootgame.unlock import UnlockSequence
    from conftest import FakeReader, HandedOff

    monkeypatch.setattr(snake.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        snake, "launch_seedsigner", lambda: (_ for _ in ()).throw(HandedOff())
    )
    reader = FakeReader([[Key.UP], [Key.LEFT], [Key.KEY1], [Key.DOWN]])

    with pytest.raises(StopIteration):
        snake.play(renderer, reader, UnlockSequence())


def test_the_screen_is_pushed_while_playing(renderer, monkeypatch):
    from bootgame.games import snake
    from bootgame.unlock import UnlockSequence
    from conftest import FakeReader

    monkeypatch.setattr(snake.time, "sleep", lambda seconds: None)
    reader = FakeReader([[], [], []])

    with pytest.raises(StopIteration):
        snake.play(renderer, reader, UnlockSequence())

    assert renderer.shown > 0
