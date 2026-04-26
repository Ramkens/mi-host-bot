"""Inline keyboards — diamond-accented premium UI."""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from app.db.models import ProductKind


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="🖤 Купить хостинг", callback_data="buy:menu"),
            InlineKeyboardButton(text="⎔ Профиль", callback_data="profile"),
        ],
        [
            InlineKeyboardButton(text="▣ Мои серверы", callback_data="instances"),
            InlineKeyboardButton(text="🛟 Поддержка", callback_data="support"),
        ],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛡️ Админка", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ В меню", callback_data="menu")]]
    )


def buy_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▪️ FunPay Cardinal · 40 ₽ / мес",
                    callback_data=f"buy:start:{ProductKind.CARDINAL.value}:std",
                )
            ],
            [
                InlineKeyboardButton(
                    text="▫️ Скрипт STD · 130 MB · 50 ₽ / мес",
                    callback_data=f"buy:start:{ProductKind.SCRIPT.value}:std",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🖤 Скрипт PRO · 512 MB · 150 ₽ / мес",
                    callback_data=f"buy:start:{ProductKind.SCRIPT.value}:pro",
                )
            ],
            [InlineKeyboardButton(text="◀️ В меню", callback_data="menu")],
        ]
    )


def buy_confirm_tier(product: str, tier: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="▭ К оплате", callback_data=f"buy:invoice:{product}:{tier}")],
            [InlineKeyboardButton(text="🎟️ У меня купон", callback_data=f"buy:coupon:{product}:{tier}")],
            [InlineKeyboardButton(text="✖️ Отмена", callback_data="menu")],
        ]
    )


def pay_buttons(pay_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="▭ Оплатить в CryptoBot (USDT)", url=pay_url)],
            [InlineKeyboardButton(text="✓ Я оплатил — проверить", callback_data="pay:check")],
            [InlineKeyboardButton(text="◀️ В меню", callback_data="menu")],
        ]
    )


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats"),
                InlineKeyboardButton(text="⎔ Юзер", callback_data="admin:user"),
            ],
            [
                InlineKeyboardButton(text="🖤 Подписки", callback_data="admin:subs"),
                InlineKeyboardButton(text="🎟️ Купоны", callback_data="admin:coupons"),
            ],
            [
                InlineKeyboardButton(text="🧩 Шарды", callback_data="admin:shards"),
                InlineKeyboardButton(text="❒ Экспорт", callback_data="admin:export"),
            ],
            [
                InlineKeyboardButton(text="🖤 Рассылка", callback_data="admin:broadcast"),
                InlineKeyboardButton(text="🛡️ Доб. админа", callback_data="admin:add_admin"),
            ],
            [
                InlineKeyboardButton(text="🎨 Брендировать канал", callback_data="admin:brand"),
                InlineKeyboardButton(text="📝 Пост", callback_data="admin:post_now"),
            ],
            [InlineKeyboardButton(text="◀️ В меню", callback_data="menu")],
        ]
    )


def admin_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ В админку", callback_data="admin")]]
    )


def admin_subs_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🖤 Выдать подписку", callback_data="admin:sub:grant")],
            [InlineKeyboardButton(text="+ Добавить дни", callback_data="admin:sub:add")],
            [InlineKeyboardButton(text="− Снять дни", callback_data="admin:sub:remove")],
            [InlineKeyboardButton(text="🚫 Отозвать подписку", callback_data="admin:sub:revoke")],
            [InlineKeyboardButton(text="◀️ В админку", callback_data="admin")],
        ]
    )


def admin_pick_product(action: str) -> InlineKeyboardMarkup:
    """For sub:grant / sub:add / sub:remove — pick which product first.

    `action` is one of {grant, add, remove}; flows through callback_data.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▪️ Cardinal",
                    callback_data=f"admin:sub:{action}:p:{ProductKind.CARDINAL.value}",
                ),
                InlineKeyboardButton(
                    text="▫️ Script STD",
                    callback_data=f"admin:sub:{action}:p:{ProductKind.SCRIPT.value}:std",
                ),
                InlineKeyboardButton(
                    text="🖤 Script PRO",
                    callback_data=f"admin:sub:{action}:p:{ProductKind.SCRIPT.value}:pro",
                ),
            ],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin:subs")],
        ]
    )


def admin_pick_days(action: str, product: str, tier: str = "std") -> InlineKeyboardMarkup:
    """Quick-pick day amounts for sub grant/add/remove."""
    rows = []
    presets = (3, 7, 14, 30, 90, 365)
    row: list[InlineKeyboardButton] = []
    for d in presets:
        row.append(
            InlineKeyboardButton(
                text=f"{d} дн",
                callback_data=f"admin:sub:{action}:d:{product}:{tier}:{d}",
            )
        )
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(
                text="✏️ Свой ввод",
                callback_data=f"admin:sub:{action}:d:{product}:{tier}:custom",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin:subs")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_coupons_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="+ Создать купон", callback_data="admin:coupon:new")],
            [InlineKeyboardButton(text="▤ Список купонов", callback_data="admin:coupon:list")],
            [InlineKeyboardButton(text="✗ Удалить купон", callback_data="admin:coupon:del")],
            [InlineKeyboardButton(text="◀️ В админку", callback_data="admin")],
        ]
    )


def admin_coupon_pick_product() -> InlineKeyboardMarkup:
    """Step 1 of new-coupon: pick product + tier."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▪️ Cardinal",
                    callback_data=f"admin:coupon:p:{ProductKind.CARDINAL.value}:std",
                )
            ],
            [
                InlineKeyboardButton(
                    text="▫️ Script STD",
                    callback_data=f"admin:coupon:p:{ProductKind.SCRIPT.value}:std",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🖤 Script PRO",
                    callback_data=f"admin:coupon:p:{ProductKind.SCRIPT.value}:pro",
                )
            ],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin:coupons")],
        ]
    )


def instance_actions(instance_id: int, product: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="▶ Старт", callback_data=f"inst:start:{instance_id}"),
            InlineKeyboardButton(text="■ Стоп", callback_data=f"inst:stop:{instance_id}"),
        ],
        [
            InlineKeyboardButton(text="↻ Рестарт", callback_data=f"inst:restart:{instance_id}"),
            InlineKeyboardButton(text="≣ Логи", callback_data=f"inst:logs:{instance_id}"),
        ],
        [
            InlineKeyboardButton(text="⟳ Статус", callback_data=f"inst:status:{instance_id}"),
            InlineKeyboardButton(text="⚙ Настроить", callback_data=f"inst:setup:{instance_id}"),
        ],
    ]
    if product == ProductKind.CARDINAL.value:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⌇ Сменить golden_key",
                    callback_data=f"inst:setkey:{instance_id}",
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="▤ Залить _main.cfg",
                    callback_data=f"inst:cfg:main:{instance_id}",
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="▤ auto_response.cfg",
                    callback_data=f"inst:cfg:resp:{instance_id}",
                ),
                InlineKeyboardButton(
                    text="▤ auto_delivery.cfg",
                    callback_data=f"inst:cfg:deliv:{instance_id}",
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="◉ Текущий _main.cfg",
                    callback_data=f"inst:cfg:show:{instance_id}",
                ),
            ]
        )
    rows.append([InlineKeyboardButton(text="◀️ К списку", callback_data="instances")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_shards_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="▤ Список", callback_data="admin:shard:list")],
            [InlineKeyboardButton(text="+ Добавить", callback_data="admin:shard:add")],
            [InlineKeyboardButton(text="⏯️ Пауза/возобновить", callback_data="admin:shard:toggle")],
            [InlineKeyboardButton(text="✗ Удалить", callback_data="admin:shard:drop")],
            [InlineKeyboardButton(text="◀️ В админку", callback_data="admin")],
        ]
    )


def admin_export_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⎔ Экспорт юзера", callback_data="admin:export:user")],
            [InlineKeyboardButton(text="❒ Экспорт всех", callback_data="admin:export:all")],
            [InlineKeyboardButton(text="◀️ В админку", callback_data="admin")],
        ]
    )
