"""Generate dark/neon brand images with Pillow.

We don't use any external AI image API — these are clean, on-brand SVG-like
compositions rendered with Pillow. Output goes to assets/.
"""
from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Iterable, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ASSETS = Path(__file__).resolve().parents[2] / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)

WIDTH = 1080
HEIGHT = 720

BG_TOP = (12, 12, 16)
BG_BOTTOM = (4, 4, 8)
NEON_PRIMARY = (88, 220, 255)  # cyan
NEON_ACCENT = (255, 80, 200)  # magenta
NEON_OK = (110, 255, 170)  # mint
TEXT_MAIN = (240, 240, 245)
TEXT_DIM = (160, 165, 180)


def _font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return ImageFont.truetype(c, size=size)
    return ImageFont.load_default()


def _gradient_bg(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h), BG_BOTTOM)
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(BG_TOP[0] * (1 - t) + BG_BOTTOM[0] * t)
        g = int(BG_TOP[1] * (1 - t) + BG_BOTTOM[1] * t)
        b = int(BG_TOP[2] * (1 - t) + BG_BOTTOM[2] * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    return img


def _add_grid(img: Image.Image, color=(28, 30, 40), step: int = 36) -> None:
    draw = ImageDraw.Draw(img)
    for x in range(0, img.width, step):
        draw.line([(x, 0), (x, img.height)], fill=color, width=1)
    for y in range(0, img.height, step):
        draw.line([(0, y), (img.width, y)], fill=color, width=1)


def _glow_text(
    img: Image.Image,
    text: str,
    pos: tuple[int, int],
    *,
    size: int,
    color=NEON_PRIMARY,
    glow_radius: int = 12,
    anchor: str = "lt",
) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text(pos, text, font=_font(size), fill=color + (255,), anchor=anchor)
    glow = layer.filter(ImageFilter.GaussianBlur(glow_radius))
    img.paste(glow, (0, 0), glow)
    d2 = ImageDraw.Draw(img)
    d2.text(pos, text, font=_font(size), fill=color, anchor=anchor)


def _neon_panel(
    img: Image.Image,
    box: tuple[int, int, int, int],
    *,
    border=NEON_PRIMARY,
    fill=(18, 20, 28),
    radius: int = 24,
    glow: int = 14,
) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(box, radius=radius, outline=border + (255,), width=4)
    glow_img = layer.filter(ImageFilter.GaussianBlur(glow))
    img.paste(glow_img, (0, 0), glow_img)
    d2 = ImageDraw.Draw(img)
    d2.rounded_rectangle(box, radius=radius, outline=border, width=3, fill=fill)


def _scatter_dots(
    img: Image.Image, n: int = 80, color=NEON_PRIMARY, opacity: int = 70
) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(n):
        x = random.randint(0, img.width - 1)
        y = random.randint(0, img.height - 1)
        r = random.randint(1, 3)
        d.ellipse([x - r, y - r, x + r, y + r], fill=color + (opacity,))
    img.paste(layer, (0, 0), layer)


def _base_card(title: str, subtitle: str, accent_color=NEON_PRIMARY) -> Image.Image:
    random.seed(hash(title) & 0xFFFF)
    img = _gradient_bg(WIDTH, HEIGHT)
    _add_grid(img)
    _scatter_dots(img, n=60, color=accent_color, opacity=80)
    # diagonal neon stripe
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.polygon(
        [(0, HEIGHT), (WIDTH, HEIGHT - 220), (WIDTH, HEIGHT - 180), (0, HEIGHT - 60)],
        fill=accent_color + (40,),
    )
    img.paste(layer, (0, 0), layer)

    _neon_panel(img, (60, 80, WIDTH - 60, HEIGHT - 80), border=accent_color)
    _glow_text(
        img,
        "MI HOST",
        (WIDTH // 2, 170),
        size=72,
        color=accent_color,
        glow_radius=14,
        anchor="mm",
    )
    _glow_text(
        img,
        title,
        (WIDTH // 2, 320),
        size=56,
        color=TEXT_MAIN,
        glow_radius=6,
        anchor="mm",
    )
    _glow_text(
        img,
        subtitle,
        (WIDTH // 2, 420),
        size=30,
        color=TEXT_DIM,
        glow_radius=4,
        anchor="mm",
    )
    # bottom tag
    _glow_text(
        img,
        "DARK · NEON · PASSIVE",
        (WIDTH // 2, HEIGHT - 130),
        size=20,
        color=accent_color,
        glow_radius=8,
        anchor="mm",
    )
    return img


def make_menu() -> Path:
    img = _base_card(
        "Главное меню",
        "Хостинг FunPay Cardinal · 40 ₽   |   Скрипты · 50 ₽",
        accent_color=NEON_PRIMARY,
    )
    out = ASSETS / "menu.png"
    img.save(out, "PNG", optimize=True)
    return out


def make_order() -> Path:
    img = _base_card(
        "Оформление заказа",
        "Оплата через CryptoBot · подписка 30 дней",
        accent_color=NEON_ACCENT,
    )
    out = ASSETS / "order.png"
    img.save(out, "PNG", optimize=True)
    return out


def make_profile() -> Path:
    img = _base_card(
        "Личный кабинет",
        "Подписка · Бонусы · Рефералы · Уровни",
        accent_color=NEON_OK,
    )
    out = ASSETS / "profile.png"
    img.save(out, "PNG", optimize=True)
    return out


def make_notifications() -> Path:
    img = _base_card(
        "Уведомления",
        "Истекает подписка · Платёж · Авто-рестарт",
        accent_color=(255, 200, 80),
    )
    out = ASSETS / "notifications.png"
    img.save(out, "PNG", optimize=True)
    return out


def make_minigame() -> Path:
    img = _base_card(
        "Мини-игра",
        "Раз в 12 часов · +1 день подписки",
        accent_color=(180, 100, 255),
    )
    out = ASSETS / "minigame.png"
    img.save(out, "PNG", optimize=True)
    return out


def generate_all() -> dict[str, Path]:
    return {
        "menu": make_menu(),
        "order": make_order(),
        "profile": make_profile(),
        "notifications": make_notifications(),
        "minigame": make_minigame(),
    }


def make_avatar(text: str = "M", out: Optional[Path] = None) -> Path:
    """Square avatar 512x512 for the channel."""
    size = 512
    img = _gradient_bg(size, size)
    _add_grid(img, step=24)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([40, 40, size - 40, size - 40], outline=NEON_PRIMARY + (255,), width=8)
    glow = layer.filter(ImageFilter.GaussianBlur(12))
    img.paste(glow, (0, 0), glow)
    d2 = ImageDraw.Draw(img)
    d2.ellipse([40, 40, size - 40, size - 40], outline=NEON_PRIMARY, width=4)
    _glow_text(img, text, (size // 2, size // 2 + 8), size=240, color=TEXT_MAIN, glow_radius=10, anchor="mm")
    if out is None:
        out = ASSETS / "avatar.png"
    img.save(out, "PNG", optimize=True)
    return out


if __name__ == "__main__":
    paths = generate_all()
    paths["avatar"] = make_avatar("M")
    for k, v in paths.items():
        print(f"{k}: {v}")
