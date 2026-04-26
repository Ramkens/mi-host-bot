"""Inline keyboards (dark/neon UI: bullets and arrows, no emoji spam)."""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.db.models import ProductKind


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="▸ Купить хостинг", callback_data="buy:menu"),
            InlineKeyboardButton(text="▸ Профиль", callback_data="profile"),
        ],
        [
            InlineKeyboardButton(text="▸ Мои инстансы", callback_data="instances"),
            InlineKeyboardButton(text="▸ Мини-игра", callback_data="minigame"),
        ],
        [
            InlineKeyboardButton(text="▸ Рефералы", callback_data="referral"),
            InlineKeyboardButton(text="▸ Поддержка", callback_data="support"),
        ],
    ]
    if is_admin:
        rows.append(
            [InlineKeyboardButton(text="▸ Админка", callback_data="admin")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="« В меню", callback_data="menu")]]
    )


def buy_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▸ Cardinal · 40 ₽",
                    callback_data=f"buy:{ProductKind.CARDINAL.value}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="▸ Кастом-скрипт · 50 ₽",
                    callback_data=f"buy:{ProductKind.SCRIPT.value}",
                )
            ],
            [InlineKeyboardButton(text="« В меню", callback_data="menu")],
        ]
    )


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="▸ Статистика", callback_data="admin:stats")],
            [InlineKeyboardButton(text="▸ Рассылка", callback_data="admin:broadcast")],
            [InlineKeyboardButton(text="▸ Доб. админа", callback_data="admin:add_admin")],
            [InlineKeyboardButton(text="▸ Брендировать канал", callback_data="admin:brand")],
            [InlineKeyboardButton(text="▸ Опубликовать пост", callback_data="admin:post_now")],
            [InlineKeyboardButton(text="« В меню", callback_data="menu")],
        ]
    )


def instance_actions(instance_id: int, product: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="▸ Старт", callback_data=f"inst:start:{instance_id}"),
            InlineKeyboardButton(text="▸ Стоп", callback_data=f"inst:stop:{instance_id}"),
        ],
        [
            InlineKeyboardButton(text="▸ Рестарт", callback_data=f"inst:restart:{instance_id}"),
            InlineKeyboardButton(text="▸ Логи", callback_data=f"inst:logs:{instance_id}"),
        ],
        [
            InlineKeyboardButton(text="▸ Статус", callback_data=f"inst:status:{instance_id}"),
        ],
    ]
    if product == ProductKind.CARDINAL.value:
        rows.append(
            [
                InlineKeyboardButton(
                    text="▸ Сменить golden_key",
                    callback_data=f"inst:setkey:{instance_id}",
                ),
            ]
        )
    rows.append([InlineKeyboardButton(text="« К списку", callback_data="instances")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pay_buttons(pay_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="▸ Оплатить", url=pay_url)],
            [InlineKeyboardButton(text="▸ Я оплатил — проверить", callback_data="pay:check")],
            [InlineKeyboardButton(text="« В меню", callback_data="menu")],
        ]
    )
