#!/usr/bin/env python3
"""A tiny terminal aquarium game.

Run:
    python terminal_aquarium.py

Controls:
    WASD or arrow keys to swim
    Q or Ctrl+C to quit
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import signal
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass

if os.name == "nt":
    import msvcrt
else:
    import select
    import termios
    import tty


WATER = "\033[38;5;39m"
FISH = "\033[38;5;214m"
BUBBLE = "\033[38;5;159m"
PLAYER = "\033[38;5;117m"
SHARK = "\033[38;5;244m"
FOOD = "\033[38;5;226m"
HURT = "\033[38;5;196m"
PLANT = "\033[38;5;76m"
SAND = "\033[38;5;220m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
CLEAR = "\033[2J\033[H"
MOVE_HOME = "\033[H"


@dataclass
class Fishy:
    x: int
    y: int
    speed: int
    direction: int
    body: str

    def swim(self, width: int) -> None:
        self.x += self.speed * self.direction
        if self.x <= 1:
            self.x = 1
            self.direction = 1
        elif self.x >= width - len(self.body) - 2:
            self.x = width - len(self.body) - 2
            self.direction = -1

    def glyph(self) -> str:
        if self.direction > 0:
            return self.body
        return self.body[::-1].translate(str.maketrans("><(){}", "<>()}{"))


@dataclass
class Bubble:
    x: int
    y: int
    drift: int

    def rise(self, width: int, height: int) -> None:
        self.y -= 1
        if random.random() < 0.35:
            self.x += self.drift
        self.x = max(1, min(width - 2, self.x))
        if self.y < 1:
            self.y = height - 3
            self.x = random.randint(2, width - 3)
            self.drift = random.choice([-1, 0, 1])


@dataclass
class PlayerFish:
    x: int
    y: int
    score: int = 0
    lives: int = 3
    invincible_until: float = 0.0

    @property
    def glyph(self) -> str:
        return "><>"

    def move(self, dx: int, dy: int, width: int, height: int) -> None:
        self.x = max(1, min(width - len(self.glyph) - 2, self.x + dx))
        self.y = max(2, min(height - 5, self.y + dy))

    def clamp(self, width: int, height: int) -> None:
        self.move(0, 0, width, height)

    def is_invincible(self) -> bool:
        return time.monotonic() < self.invincible_until


@dataclass
class FoodPellet:
    x: int
    y: int

    def fall(self, width: int, height: int) -> None:
        self.y += 1
        if random.random() < 0.2:
            self.x += random.choice([-1, 1])
        self.x = max(1, min(width - 2, self.x))
        if self.y >= height - 3:
            self.respawn(width)

    def respawn(self, width: int) -> None:
        self.x = random.randint(2, width - 3)
        self.y = 2

    def clamp(self, width: int, height: int) -> None:
        self.x = max(1, min(width - 2, self.x))
        self.y = max(2, min(height - 4, self.y))


@dataclass
class Shark:
    x: int
    y: int
    direction: int
    speed: int

    @property
    def glyph(self) -> str:
        return "<====>" if self.direction < 0 else "<====>"

    def swim(self, width: int, height: int, level: int) -> None:
        self.x += self.direction * self.speed
        if self.x < -len(self.glyph) or self.x > width + len(self.glyph):
            self.direction = random.choice([-1, 1])
            self.y = random.randint(2, height - 5)
            self.speed = random.choice([1, 1, 2]) + level // 6
            self.x = width - 2 if self.direction < 0 else -len(self.glyph) + 1

    def clamp(self, height: int) -> None:
        self.y = max(2, min(height - 5, self.y))


def enable_windows_ansi() -> None:
    if os.name == "nt":
        os.system("")


def term_size() -> tuple[int, int]:
    size = shutil.get_terminal_size((80, 24))
    width = max(40, size.columns)
    height = max(15, size.lines)
    return width, height


def put(canvas: list[list[str]], x: int, y: int, text: str) -> None:
    if y < 0 or y >= len(canvas):
        return
    for offset, ch in enumerate(text):
        px = x + offset
        if 0 <= px < len(canvas[y]):
            canvas[y][px] = ch


@contextmanager
def raw_terminal() -> object:
    if os.name == "nt":
        yield
        return

    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        yield
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)


def read_key() -> str | None:
    if os.name == "nt":
        if not msvcrt.kbhit():
            return None
        key = msvcrt.getch()
        if key in (b"\x00", b"\xe0"):
            arrow = msvcrt.getch()
            return {
                b"H": "up",
                b"P": "down",
                b"K": "left",
                b"M": "right",
            }.get(arrow)
        try:
            return key.decode("utf-8").lower()
        except UnicodeDecodeError:
            return None

    ready, _, _ = select.select([sys.stdin], [], [], 0)
    if not ready:
        return None
    key = sys.stdin.read(1)
    if key == "\x1b" and select.select([sys.stdin], [], [], 0)[0]:
        sequence = sys.stdin.read(2)
        return {
            "[A": "up",
            "[B": "down",
            "[D": "left",
            "[C": "right",
        }.get(sequence)
    return key.lower()


def overlaps(ax: int, ay: int, aw: int, bx: int, by: int, bw: int) -> bool:
    if ay != by:
        return False
    return ax < bx + bw and bx < ax + aw


def make_fish(width: int, height: int) -> list[Fishy]:
    bodies = ["><(((('>", "><>", "><((*>", "><{{{*>", "><)))'>"]
    count = max(5, min(14, width // 10))
    fish = []
    for _ in range(count):
        body = random.choice(bodies)
        fish.append(
            Fishy(
                x=random.randint(1, max(2, width - len(body) - 2)),
                y=random.randint(2, height - 5),
                speed=random.choice([1, 1, 1, 2]),
                direction=random.choice([-1, 1]),
                body=body,
            )
        )
    return fish


def make_bubbles(width: int, height: int) -> list[Bubble]:
    return [
        Bubble(
            x=random.randint(2, width - 3),
            y=random.randint(2, height - 4),
            drift=random.choice([-1, 0, 1]),
        )
        for _ in range(max(10, width // 5))
    ]


def make_food(width: int) -> list[FoodPellet]:
    return [FoodPellet(random.randint(2, width - 3), random.randint(2, 8)) for _ in range(5)]


def make_sharks(width: int, height: int) -> list[Shark]:
    return [
        Shark(
            x=random.choice([-8, width + 4]),
            y=random.randint(3, height - 6),
            direction=random.choice([-1, 1]),
            speed=1,
        )
        for _ in range(2)
    ]


def draw_frame(
    fish: list[Fishy],
    bubbles: list[Bubble],
    player: PlayerFish,
    foods: list[FoodPellet],
    sharks: list[Shark],
    tick: int,
    level: int,
    message: str,
    use_color: bool,
) -> str:
    width, height = term_size()
    canvas = [[" " for _ in range(width)] for _ in range(height)]

    hearts = "<3 " * player.lives
    title = f" score {player.score:04d} | lives {hearts.strip()} | level {level} | WASD/arrows swim | Q quit "
    put(canvas, max(0, (width - len(title)) // 2), 0, title)

    wave = "~" * width
    put(canvas, 0, 1, wave)

    for x in range(0, width, 7):
        sway = "/" if (tick + x) % 8 < 4 else "\\"
        plant = sway + "|" + sway
        put(canvas, x, height - 4, plant)
        put(canvas, x, height - 3, " | ")

    put(canvas, 0, height - 2, "." * width)
    put(canvas, 0, height - 1, "_" * width)

    for bubble in bubbles:
        put(canvas, bubble.x, bubble.y, random.choice(["o", "O", "."]))

    for food in foods:
        put(canvas, food.x, food.y, "*")

    for swimmer in fish:
        put(canvas, swimmer.x, swimmer.y, swimmer.glyph())

    for shark in sharks:
        put(canvas, shark.x, shark.y, shark.glyph)

    if not player.is_invincible() or tick % 2 == 0:
        put(canvas, player.x, player.y, player.glyph)

    if message:
        put(canvas, max(0, (width - len(message)) // 2), height - 3, message[:width])

    if not use_color:
        return "\n".join("".join(row) for row in canvas)

    rendered = []
    for y, row in enumerate(canvas):
        line = "".join(row)
        if y in (0, 1):
            rendered.append(f"{WATER}{DIM}{line}{RESET}")
        elif y >= height - 2:
            rendered.append(f"{SAND}{line}{RESET}")
        else:
            colored = []
            for ch in line:
                if ch == "*":
                    colored.append(f"{FOOD}{BOLD}{ch}{RESET}")
                elif ch in "oO.":
                    colored.append(f"{BUBBLE}{ch}{RESET}")
                elif ch in "/\\|":
                    colored.append(f"{PLANT}{ch}{RESET}")
                elif ch == "=":
                    colored.append(f"{SHARK}{ch}{RESET}")
                elif ch.strip():
                    colored.append(f"{FISH}{ch}{RESET}")
                else:
                    colored.append(ch)
            rendered.append("".join(colored))
    return "\n".join(rendered)


def draw_game_over(score: int, use_color: bool) -> str:
    width, height = term_size()
    lines = [
        "",
        "      ___                 ___",
        "     / _ \\__ _ _ __ ___  / _ \\__   _____ _ __",
        "    / /_\\/ _` | '_ ` _ \\/ /_)/\\ \\ / / _ \\ '__|",
        "   / /_\\\\ (_| | | | | | / ___/  \\ V /  __/ |",
        "   \\____/\\__,_|_| |_| |_\\/       \\_/ \\___|_|",
        "",
        f"              Final score: {score}",
        "",
        "        再来一局：python terminal_aquarium.py",
    ]
    top = max(0, (height - len(lines)) // 2)
    output = [""] * top
    for line in lines:
        output.append(line.center(width))
    text = "\n".join(output)
    return f"{HURT}{BOLD}{text}{RESET}" if use_color else text


def run(frames: int | None, delay: float, use_color: bool) -> None:
    width, height = term_size()
    fish = make_fish(width, height)
    bubbles = make_bubbles(width, height)
    player = PlayerFish(width // 2, height // 2)
    foods = make_food(width)
    sharks = make_sharks(width, height)
    tick = 0
    next_shark_score = 120
    message = "吃掉 * 得分，避开鲨鱼 <====>"

    def restore_cursor(*_: object) -> None:
        if use_color:
            sys.stdout.write(SHOW_CURSOR + RESET)
            sys.stdout.flush()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, restore_cursor)

    if use_color:
        sys.stdout.write(HIDE_CURSOR)
    try:
        sys.stdout.write(CLEAR)
        with raw_terminal():
            while player.lives > 0 and (frames is None or tick < frames):
                width, height = term_size()
                player.clamp(width, height)
                for food in foods:
                    food.clamp(width, height)
                for shark in sharks:
                    shark.clamp(height)

                key = read_key()
                if key in ("q", "\x03"):
                    break
                if key in ("w", "up"):
                    player.move(0, -1, width, height)
                elif key in ("s", "down"):
                    player.move(0, 1, width, height)
                elif key in ("a", "left"):
                    player.move(-2, 0, width, height)
                elif key in ("d", "right"):
                    player.move(2, 0, width, height)

                level = 1 + player.score // 80
                for swimmer in fish:
                    swimmer.swim(width)
                for bubble in bubbles:
                    bubble.rise(width, height)
                for food in foods:
                    if tick % max(2, 7 - level) == 0:
                        food.fall(width, height)
                    if overlaps(player.x, player.y, len(player.glyph), food.x, food.y, 1):
                        player.score += 10
                        message = random.choice(["好吃！", "泡泡加餐！", "小鱼正在长大！", "+10"])
                        food.respawn(width)

                for shark in sharks:
                    shark.swim(width, height, level)
                    if (
                        not player.is_invincible()
                        and overlaps(player.x, player.y, len(player.glyph), shark.x, shark.y, len(shark.glyph))
                    ):
                        player.lives -= 1
                        player.invincible_until = time.monotonic() + 1.5
                        message = "被鲨鱼擦到了！闪烁时短暂无敌"

                if player.score >= next_shark_score and len(sharks) < 5:
                    sharks.append(Shark(width + 4, random.randint(3, height - 6), -1, 1))
                    next_shark_score += 120
                    message = "水流变急了，鲨鱼更多了！"

                sys.stdout.write(
                    MOVE_HOME
                    + draw_frame(fish, bubbles, player, foods, sharks, tick, level, message, use_color)
                )
                sys.stdout.flush()
                time.sleep(delay)
                tick += 1

        sys.stdout.write(CLEAR + draw_game_over(player.score, use_color) + "\n")
        sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        if use_color:
            sys.stdout.write(SHOW_CURSOR + RESET + "\n")
            sys.stdout.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A tiny terminal aquarium game.")
    parser.add_argument("--frames", type=int, default=None, help="Render N frames, then exit.")
    parser.add_argument("--delay", type=float, default=0.06, help="Delay between frames.")
    parser.add_argument("--seed", type=int, default=None, help="Use a fixed random seed.")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.seed is not None:
        random.seed(args.seed)
    enable_windows_ansi()
    run(args.frames, args.delay, not args.no_color)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
