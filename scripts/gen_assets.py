"""Pre-render all brand assets (called at build time on Render)."""
from __future__ import annotations

from app.services.images import generate_all, make_avatar


def main() -> None:
    paths = generate_all()
    paths["avatar"] = make_avatar("M")
    for k, v in paths.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
