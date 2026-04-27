"""Channel autopilot: branding + auto-content + scheduling.

Bot must be admin in `settings.channel_id`. We use:
* `setChatPhoto` for avatar
* `setChatTitle`, `setChatDescription` for bio
* `pinChatMessage` for the welcome pin
* `sendMessage` / `sendPhoto` for content
"""
from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from aiogram import Bot
from aiogram.types import FSInputFile

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import ContentPost
from app.services.content_gen import generate, GeneratedPost
from app.services.images import ASSETS, make_avatar, generate_all
from app.utils.time import now_utc

logger = logging.getLogger(__name__)


async def auto_brand(bot: Bot) -> bool:
    """Set channel avatar / title / description / pin once. Idempotent."""
    if not settings.channel_id:
        logger.info("channel autopilot: CHANNEL_ID not set — skip")
        return False
    chat_id = settings.channel_id
    me = await bot.get_me()
    bot_username = me.username or "MiHostingBot"

    # Avatar
    avatar_path = ASSETS / "avatar.png"
    if not avatar_path.exists():
        make_avatar("M", out=avatar_path)
    try:
        await bot.set_chat_photo(chat_id, photo=FSInputFile(str(avatar_path)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_chat_photo: %s", exc)

    title = "Mi Host — хостинг FunPay Cardinal"
    description = (
        "Хостинг FunPay Cardinal за 40 ₽/мес.\n\n"
        "Авто-настройка · Авто-рестарт · Логи · Оплата CryptoBot.\n\n"
        f"Бот: @{bot_username}"
    )
    try:
        await bot.set_chat_title(chat_id, title)
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_chat_title: %s", exc)
    try:
        await bot.set_chat_description(chat_id, description)
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_chat_description: %s", exc)

    # Pinned welcome
    welcome = (
        "<b>Mi Host — хостинг FunPay Cardinal.</b>\n\n"
        "— 40 ₽ / 30 дней\n"
        "— Авто-настройка, авто-рестарт, логи, мониторинг\n"
        "— Оплата CryptoBot · подписка 30 дней\n\n"
        f"Открыть бот: @{bot_username}"
    )
    try:
        msg = await bot.send_message(chat_id, welcome, parse_mode="HTML")
        await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("pin welcome: %s", exc)
    return True


KIND_TO_IMAGE = {
    "post": "menu.png",
    "review": "profile.png",
    "case": "order.png",
    "update": "notifications.png",
}


async def post_one(bot: Bot, kind: Optional[str] = None) -> Optional[int]:
    if not settings.channel_id:
        return None
    if kind is None:
        kind = random.choices(
            ["post", "review", "case", "update"],
            weights=[5, 3, 2, 2],
            k=1,
        )[0]
    me = await bot.get_me()
    post: GeneratedPost = await generate(kind, bot_username=me.username or "MiHostingBot")
    text = f"<b>{post.title}</b>\n\n{post.body}\n\n{post.cta}"
    img_path = ASSETS / KIND_TO_IMAGE.get(kind, "menu.png")
    if not img_path.exists():
        generate_all()
    try:
        if img_path.exists():
            msg = await bot.send_photo(
                settings.channel_id,
                photo=FSInputFile(str(img_path)),
                caption=text,
                parse_mode="HTML",
            )
        else:
            msg = await bot.send_message(
                settings.channel_id, text, parse_mode="HTML"
            )
        async with SessionLocal() as s:
            s.add(
                ContentPost(
                    kind=kind,
                    title=post.title,
                    body=post.body,
                    image_path=str(img_path) if img_path.exists() else None,
                    posted_at=now_utc(),
                    tg_message_id=msg.message_id,
                )
            )
            await s.commit()
        return msg.message_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("post_one failed: %s", exc)
        return None
