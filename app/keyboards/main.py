"""Inline keyboards (strict dark/neon UI: arrows + bullets, no joyful emoji)."""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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
            InlineKeyboardButton(text="▸ Поддержка", callback_data="support"),
        ],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="◆ Админка", callback_data="admin")])
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
                    text="▸ FunPay Cardinal · 40 ₽ / мес",
                    callback_data=f"buy:start:{ProductKind.CARDINAL.value}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="▸ Кастом-скрипт · 50 ₽ / мес",
                    callback_data=f"buy:start:{ProductKind.SCRIPT.value}",
                )
            ],
            [InlineKeyboardButton(text="« В меню", callback_data="menu")],
        ]
    )


def buy_confirm(product: str) -> InlineKeyboardMarkup:
    """After settings collected — confirm before invoice."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="▣ К оплате", callback_data=f"buy:invoice:{product}")],
            [InlineKeyboardButton(text="◇ У меня купон", callback_data=f"buy:coupon:{product}")],
            [InlineKeyboardButton(text="« Отмена", callback_data="menu")],
        ]
    )


def pay_buttons(pay_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="▣ Оплатить в CryptoBot (USDT)", url=pay_url)],
            [InlineKeyboardButton(text="▸ Я оплатил — проверить", callback_data="pay:check")],
            [InlineKeyboardButton(text="◇ Другая крипта → саппорт", callback_data="support")],
            [InlineKeyboardButton(text="« В меню", callback_data="menu")],
        ]
    )


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◆ Статистика", callback_data="admin:stats")],
            [InlineKeyboardButton(text="◆ Купоны", callback_data="admin:coupons")],
            [InlineKeyboardButton(text="◆ Подписки", callback_data="admin:subs_help")],
            [InlineKeyboardButton(text="◆ Шарды", callback_data="admin:shards")],
            [InlineKeyboardButton(text="◆ Рассылка", callback_data="admin:broadcast")],
            [InlineKeyboardButton(text="◆ Доб. админа", callback_data="admin:add_admin")],
            [InlineKeyboardButton(text="◆ Брендировать канал", callback_data="admin:brand")],
            [InlineKeyboardButton(text="◆ Опубликовать пост", callback_data="admin:post_now")],
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
