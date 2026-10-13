"""
Snake.

Owns its state, its rendering and its loop. Nothing outside this module knows
how Snake works.
"""

import logging
import time
from random import Random
from typing import List, Optional, Tuple

from PIL import ImageDraw

from bootgame.display import BACKGROUND, Colour, centred_text
from bootgame.keys import Key
from bootgame.launch import launch_seedsigner

logger = logging.getLogger(__name__)

# (x, y), origin top left
Cell = Tuple[int, int]

DIRECTIONS = {
    Key.UP: (0, -1),
    Key.DOWN: (0, 1),
    Key.LEFT: (-1, 0),
    Key.RIGHT: (1, 0),
}

SNAKE_HEAD: Colour = (0, 255, 0)
SNAKE_BODY: Colour = (0, 160, 0)
FOOD: Colour = (255, 159, 10)

# Leaves a hairline gap between cells so the body reads as segments.
CELL_INSET = 1

# The board takes as many cells as the panel holds: 12x12 on the 240x240 hat,
# 16x12 on the 320x240 one. A fixed 12x12 left black bars down both sides there.
CELL_PIXELS = 20

TICK_SECONDS = 0.18
POLL_SECONDS = 0.01
GAME_OVER_PAUSE_SECONDS = 1.5


class SnakeGame:
    """
    Grid-based Snake as a pure state machine.

    No I/O, no wall clock: every transition is driven by an explicit `tick()`
    and randomness is injected, so this is deterministic and unit testable.
    `food` is None only when the board has been filled, which is a win.
    """

    def __init__(self, grid_width: int = 12, grid_height: int = 12, rng: Random = None):
        self.grid_width = grid_width
        self.grid_height = grid_height
        self.rng = rng if rng is not None else Random()
        self.reset()


    def reset(self) -> None:
        """Return to the opening position: a length 3 snake heading right."""
        middle_row = self.grid_height // 2
        self.snake: List[Cell] = [(2, middle_row), (1, middle_row), (0, middle_row)]
        self.direction: Cell = DIRECTIONS[Key.RIGHT]
        self.score = 0
        self.game_over = False
        self.food = self._place_food()


    def turn(self, key: Key) -> None:
        """
        Steer. Non-directional keys are ignored, as is a reversal straight back
        onto the neck, which would otherwise be an instant loss.
        """
        new_direction = DIRECTIONS.get(key)
        if new_direction is None:
            return

        if (-new_direction[0], -new_direction[1]) == self.direction:
            return

        self.direction = new_direction


    def tick(self) -> None:
        """Advance one step. Terminal once `game_over` is set."""
        if self.game_over:
            return

        head_x, head_y = self.snake[0]
        new_head = (head_x + self.direction[0], head_y + self.direction[1])

        if not self._in_bounds(new_head):
            self.game_over = True
            return

        eating = new_head == self.food

        # The tail vacates its cell on this same tick, so it only blocks the
        # head when the snake is about to grow into it.
        body = self.snake if eating else self.snake[:-1]
        if new_head in body:
            self.game_over = True
            return

        self.snake.insert(0, new_head)
        if eating:
            self.score += 1
            self.food = self._place_food()
        else:
            self.snake.pop()


    def _in_bounds(self, cell: Cell) -> bool:
        x, y = cell
        return 0 <= x < self.grid_width and 0 <= y < self.grid_height


    def _place_food(self) -> Optional[Cell]:
        occupied = set(self.snake)
        free = [
            (x, y)
            for y in range(self.grid_height)
            for x in range(self.grid_width)
            if (x, y) not in occupied
        ]
        if not free:
            return None

        return self.rng.choice(free)


def render(draw: ImageDraw.ImageDraw, game: SnakeGame, width: int, height: int) -> None:
    """Render one frame. The board is centred and square, whatever the canvas."""
    draw.rectangle((0, 0, width, height), fill=BACKGROUND)

    cell_size = min(width // game.grid_width, height // game.grid_height)
    origin_x = (width - cell_size * game.grid_width) // 2
    origin_y = (height - cell_size * game.grid_height) // 2

    if game.food is not None:
        _draw_cell(draw, game.food, cell_size, origin_x, origin_y, FOOD)

    for index, segment in enumerate(game.snake):
        _draw_cell(
            draw,
            segment,
            cell_size,
            origin_x,
            origin_y,
            SNAKE_HEAD if index == 0 else SNAKE_BODY,
        )

    if game.game_over:
        centred_text(draw, "GAME OVER", width, height)


def _draw_cell(draw, cell: Cell, cell_size: int, origin_x: int, origin_y: int, colour: Colour) -> None:
    x, y = cell
    left = origin_x + x * cell_size
    top = origin_y + y * cell_size
    draw.rectangle(
        (left, top, left + cell_size - CELL_INSET, top + cell_size - CELL_INSET),
        fill=colour,
    )


def play(renderer, reader, unlock) -> None:
    """Play until the unlock sequence is entered. Does not return."""
    game = SnakeGame(
        grid_width=renderer.canvas_width // CELL_PIXELS,
        grid_height=renderer.canvas_height // CELL_PIXELS,
    )
    next_tick = time.monotonic()

    while True:
        for key in reader.presses():
            if unlock.feed(key):
                launch_seedsigner()

            game.turn(key)

        now = time.monotonic()
        if now >= next_tick:
            game.tick()
            render(renderer.draw, game, renderer.canvas_width, renderer.canvas_height)
            renderer.show_image()

            if game.game_over:
                time.sleep(GAME_OVER_PAUSE_SECONDS)
                game.reset()

            next_tick = time.monotonic() + TICK_SECONDS

        time.sleep(POLL_SECONDS)
