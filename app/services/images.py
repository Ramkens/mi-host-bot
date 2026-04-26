"""Dark/neon brand images rendered with Pillow.

Cleaner second-pass: typographic emphasis, terminal-style accents,
distinct per-asset layouts, and no rainbow / joyful flourishes.
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ASSETS = Path(__file__).resolve().parents[2] / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)

WIDTH = 1280
HEIGHT = 720

# Strict palette: deep black + monochrome cool grey + a single neon cyan accent.
BG_TOP = (10, 11, 14)
BG_BOTTOM = (2, 2, 4)
PANEL_FILL = (14, 16, 22)
PANEL_INNER = (8, 10, 14)
NEON = (96, 220, 255)        # the only chromatic accent
NEON_DIM = (40, 110, 140)
TEXT_MAIN = (236, 238, 242)
TEXT_DIM = (148, 156, 170)
TEXT_FAINT = (78, 86, 100)
GRID = (22, 24, 32)


def _font(size: int, *, bold: bool = True, mono: bool = False) -> ImageFont.FreeTypeFont:
    candidates: list[str]
    if mono:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        ]
    else:
        candidates = (
            [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            ]
            if bold
            else [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            ]
        )
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


def _grid(img: Image.Image, step: int = 40) -> None:
    d = ImageDraw.Draw(img)
    for x in range(0, img.width, step):
        d.line([(x, 0), (x, img.height)], fill=GRID, width=1)
    for y in range(0, img.height, step):
        d.line([(0, y), (img.width, y)], fill=GRID, width=1)


def _scanlines(img: Image.Image, alpha: int = 22) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for y in range(0, img.height, 3):
        d.line([(0, y), (img.width, y)], fill=(0, 0, 0, alpha), width=1)
    img.paste(layer, (0, 0), layer)


def _glow_text(
    img: Image.Image,
    text: str,
    pos: tuple[int, int],
    *,
    size: int,
    color=TEXT_MAIN,
    glow=NEON,
    glow_radius: int = 10,
    glow_alpha: int = 220,
    anchor: str = "lt",
    mono: bool = False,
    bold: bool = True,
) -> None:
    font = _font(size, bold=bold, mono=mono)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text(pos, text, font=font, fill=glow + (glow_alpha,), anchor=anchor)
    blurred = layer.filter(ImageFilter.GaussianBlur(glow_radius))
    img.paste(blurred, (0, 0), blurred)
    d2 = ImageDraw.Draw(img)
    d2.text(pos, text, font=font, fill=color, anchor=anchor)


def _panel(
    img: Image.Image,
    box: tuple[int, int, int, int],
    *,
    border=NEON,
    fill=PANEL_FILL,
    radius: int = 18,
    glow_radius: int = 10,
    border_alpha: int = 200,
) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(box, radius=radius, outline=border + (border_alpha,), width=4)
    blurred = layer.filter(ImageFilter.GaussianBlur(glow_radius))
    img.paste(blurred, (0, 0), blurred)
    d2 = ImageDraw.Draw(img)
    d2.rounded_rectangle(box, radius=radius, outline=border, width=2, fill=fill)


def _badge(img: Image.Image, x: int, y: int, label: str, *, accent=NEON) -> None:
    pad = 14
    font = _font(20, bold=True, mono=True)
    bbox = ImageDraw.Draw(img).textbbox((0, 0), label, font=font)
    w = bbox[2] - bbox[0] + pad * 2
    h = bbox[3] - bbox[1] + pad
    box = (x, y, x + w, y + h)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(box, radius=8, fill=accent + (32,), outline=accent + (220,), width=1)
    img.paste(layer, (0, 0), layer)
    ImageDraw.Draw(img).text(
        (x + pad, y + h // 2), label, font=font, fill=accent, anchor="lm"
    )


def _vertical_bars(img: Image.Image, x: int, y: int, w: int, h: int, *, n: int = 14) -> None:
    """Decorative side rail of vertical bars."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    bar_w = max(2, w // (n * 2))
    gap = (w - bar_w * n) // max(1, n - 1)
    rng = random.Random(x * 31 + y * 17)
    for i in range(n):
        bx = x + i * (bar_w + gap)
        bh = rng.randint(h // 6, h)
        alpha = rng.randint(40, 220)
        d.rounded_rectangle(
            (bx, y + h - bh, bx + bar_w, y + h),
            radius=2, fill=NEON + (alpha,),
        )
    img.paste(layer, (0, 0), layer)


def _terminal_lines(img: Image.Image, x: int, y: int, lines: list[str], *, size: int = 22) -> None:
    """Render a list of terminal-style lines starting at (x, y)."""
    font = _font(size, bold=False, mono=True)
    d = ImageDraw.Draw(img)
    line_h = size + 8
    for i, line in enumerate(lines):
        color = TEXT_DIM if line.startswith(("#", "//")) else TEXT_MAIN
        d.text((x, y + i * line_h), line, font=font, fill=color)


def _build_base() -> Image.Image:
    img = _gradient_bg(WIDTH, HEIGHT)
    _grid(img, step=40)
    # Vignette
    vignette = Image.new("RGBA", img.size, (0, 0, 0, 0))
    vd = ImageDraw.Draw(vignette)
    for r in range(0, 300, 2):
        a = int(180 * (r / 300))
        vd.ellipse(
            (-r, -r, img.width + r, img.height + r),
            outline=(0, 0, 0, max(0, 220 - a)),
            width=2,
        )
    blurred = vignette.filter(ImageFilter.GaussianBlur(60))
    img.paste(blurred, (0, 0), blurred)
    _scanlines(img, alpha=18)
    return img


def _wordmark(img: Image.Image, x: int, y: int) -> None:
    _glow_text(img, "MI", (x, y), size=84, color=TEXT_MAIN, glow=NEON, anchor="lt")
    _glow_text(img, "HOST", (x + 130, y), size=84, color=NEON, glow=NEON, anchor="lt")
    # Tag below wordmark
    _glow_text(
        img,
        "// HOSTING TERMINAL",
        (x + 4, y + 100),
        size=18,
        color=TEXT_FAINT,
        glow=NEON_DIM,
        glow_radius=4,
        anchor="lt",
        mono=True,
        bold=False,
    )


# ---- Per-asset compositions ----


def make_menu() -> Path:
    img = _build_base()
    _wordmark(img, 80, 80)

    # Right-side rail
    _vertical_bars(img, WIDTH - 360, 80, 280, 120, n=18)

    # Hero panel
    _panel(img, (80, 240, WIDTH - 80, HEIGHT - 80), border=NEON, glow_radius=14)
    _glow_text(
        img, "ГЛАВНОЕ МЕНЮ", (110, 270),
        size=44, color=TEXT_MAIN, glow=NEON, glow_radius=8, anchor="lt",
    )
    _terminal_lines(
        img, 110, 340,
        [
            "$ mi-host status",
            "> online · 24/7",
            "> tenants: ready",
            "> payments: cryptobot · usdt",
            "",
            "$ mi-host menu --open",
        ],
        size=24,
    )
    _badge(img, 110, HEIGHT - 140, "FUNPAY CARDINAL · 40 ₽")
    _badge(img, 470, HEIGHT - 140, "CUSTOM SCRIPT · 50 ₽")
    out = ASSETS / "menu.png"
    img.save(out, "PNG", optimize=True)
    return out


def make_order() -> Path:
    img = _build_base()
    _wordmark(img, 80, 80)
    _panel(img, (80, 240, WIDTH - 80, HEIGHT - 80))
    _glow_text(
        img, "ЗАКАЗ", (110, 270),
        size=48, color=TEXT_MAIN, glow=NEON, anchor="lt",
    )
    _terminal_lines(
        img, 110, 350,
        [
            "# Шаг 1 собрать настройки",
            "# Шаг 2 подтвердить заказ",
            "# Шаг 3 оплата (USDT через CryptoBot)",
            "",
            "$ mi-host order --create",
            "> status: awaiting input",
        ],
    )
    _badge(img, 110, HEIGHT - 140, "USDT ONLY · CRYPTO BOT")
    _badge(img, 480, HEIGHT - 140, "OTHER COIN → /support")
    out = ASSETS / "order.png"
    img.save(out, "PNG", optimize=True)
    return out


def make_profile() -> Path:
    img = _build_base()
    _wordmark(img, 80, 80)
    _panel(img, (80, 240, WIDTH - 80, HEIGHT - 80))
    _glow_text(
        img, "ПРОФИЛЬ", (110, 270),
        size=48, color=TEXT_MAIN, glow=NEON, anchor="lt",
    )
    _terminal_lines(
        img, 110, 350,
        [
            "user.id : ********",
            "subscriptions : см. ниже",
            "instances : живые / уснувшие",
            "auto-restart : on",
            "auto-renew : ручной (через купон или оплата)",
        ],
    )
    out = ASSETS / "profile.png"
    img.save(out, "PNG", optimize=True)
    return out


def make_notifications() -> Path:
    img = _build_base()
    _wordmark(img, 80, 80)
    _panel(img, (80, 240, WIDTH - 80, HEIGHT - 80))
    _glow_text(
        img, "УВЕДОМЛЕНИЯ", (110, 270),
        size=44, color=TEXT_MAIN, glow=NEON, anchor="lt",
    )
    _terminal_lines(
        img, 110, 350,
        [
            "[T-3 days] подписка истекает — сохрани конфиги",
            "[paid] оплата прошла — инстанс запущен",
            "[restart] инстанс упал — перезапуск автоматом",
            "[rotate] обновление серверов — данные сохранены",
        ],
    )
    out = ASSETS / "notifications.png"
    img.save(out, "PNG", optimize=True)
    return out


def generate_all() -> dict[str, Path]:
    return {
        "menu": make_menu(),
        "order": make_order(),
        "profile": make_profile(),
        "notifications": make_notifications(),
    }


def make_avatar(text: str = "M", out: Optional[Path] = None) -> Path:
    """Square 512×512 channel avatar."""
    size = 512
    img = _gradient_bg(size, size)
    _grid(img, step=24)
    # Outer ring
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([28, 28, size - 28, size - 28], outline=NEON + (220,), width=10)
    blurred = layer.filter(ImageFilter.GaussianBlur(14))
    img.paste(blurred, (0, 0), blurred)
    d2 = ImageDraw.Draw(img)
    d2.ellipse([28, 28, size - 28, size - 28], outline=NEON, width=4)
    # Inner panel
    d2.rectangle([110, 200, size - 110, 312], fill=PANEL_INNER, outline=NEON, width=2)
    _glow_text(
        img, "MI", (size // 2 - 70, size // 2 + 6),
        size=120, color=TEXT_MAIN, glow=NEON, glow_radius=12, anchor="mm",
    )
    _glow_text(
        img, "HOST", (size // 2 + 50, size // 2 + 6),
        size=120, color=NEON, glow=NEON, glow_radius=12, anchor="mm",
    )
    if out is None:
        out = ASSETS / "avatar.png"
    img.save(out, "PNG", optimize=True)
    return out


if __name__ == "__main__":
    paths = generate_all()
    paths["avatar"] = make_avatar("M")
    for k, v in paths.items():
        print(f"{k}: {v}")
