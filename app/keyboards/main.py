"""Inline keyboards. Minimalist — only essential buttons."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


# ---------------------------------------------------------------------------
# User-facing menus
# ---------------------------------------------------------------------------


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Главное меню: 3 кнопки — Серверы, Купить, Поддержка (+ Админка)."""
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="Мои серверы", callback_data="instances")],
        [InlineKeyboardButton(text="Купить сервер", callback_data="buy:menu")],
        [InlineKeyboardButton(text="Поддержка", callback_data="support")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="Админка", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="« В меню", callback_data="menu")]]
    )


def buy_menu() -> InlineKeyboardMarkup:
    """Один продукт — FunPay Cardinal. Купон — только на шаге оплаты."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="FunPay Cardinal — 40 ₽ / 30 дней",
                    callback_data="buy:start:cardinal",
                )
            ],
            [InlineKeyboardButton(text="« В меню", callback_data="menu")],
        ]
    )


def buy_confirm() -> InlineKeyboardMarkup:
    """Сводка перед оплатой."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оплатить", callback_data="buy:invoice")],
            [InlineKeyboardButton(text="У меня есть купон", callback_data="buy:coupon")],
            [InlineKeyboardButton(text="« Отмена", callback_data="menu")],
        ]
    )


def buy_cancel() -> InlineKeyboardMarkup:
    """Cancel an in-progress purchase wizard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="« Отмена", callback_data="menu")],
        ]
    )


def buy_locale() -> InlineKeyboardMarkup:
    """Pick UI/automated message language for Cardinal."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Русский", callback_data="buy:locale:ru"),
                InlineKeyboardButton(text="English", callback_data="buy:locale:en"),
                InlineKeyboardButton(text="Українська", callback_data="buy:locale:uk"),
            ],
        ]
    )


def pay_buttons(pay_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оплатить (USDT)", url=pay_url)],
            [InlineKeyboardButton(text="Я оплатил — проверить", callback_data="pay:check")],
            [InlineKeyboardButton(text="« В меню", callback_data="menu")],
        ]
    )


# ---------------------------------------------------------------------------
# Server (instance) actions
# ---------------------------------------------------------------------------


def instance_actions(instance_id: int) -> InlineKeyboardMarkup:
    """Действия с конкретным сервером пользователя."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Перезапустить",
                    callback_data=f"inst:restart:{instance_id}",
                ),
                InlineKeyboardButton(
                    text="Логи",
                    callback_data=f"inst:logs:{instance_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Настройки",
                    callback_data=f"inst:settings:{instance_id}",
                ),
                InlineKeyboardButton(
                    text="Конфиги Cardinal",
                    callback_data=f"inst:cfg:menu:{instance_id}",
                ),
            ],
            [InlineKeyboardButton(text="« К списку", callback_data="instances")],
        ]
    )


def instance_settings(instance_id: int) -> InlineKeyboardMarkup:
    """Подменю «Настройки сервера» — редактор каждого параметра."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Сменить golden_key",
                    callback_data=f"inst:edit:gk:{instance_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Сменить Telegram-бот токен",
                    callback_data=f"inst:edit:tg:{instance_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Сменить пароль доступа",
                    callback_data=f"inst:edit:pw:{instance_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Сменить язык",
                    callback_data=f"inst:edit:loc:{instance_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Удалить сервер",
                    callback_data=f"inst:delete:{instance_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="« Назад",
                    callback_data=f"inst:open:{instance_id}",
                )
            ],
        ]
    )


def instance_locale_picker(instance_id: int) -> InlineKeyboardMarkup:
    """Выбор языка при смене параметра locale."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Русский", callback_data=f"inst:setloc:{instance_id}:ru"
                ),
                InlineKeyboardButton(
                    text="English", callback_data=f"inst:setloc:{instance_id}:en"
                ),
                InlineKeyboardButton(
                    text="Українська", callback_data=f"inst:setloc:{instance_id}:uk"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="« Назад", callback_data=f"inst:settings:{instance_id}"
                )
            ],
        ]
    )


def instance_edit_cancel(instance_id: int) -> InlineKeyboardMarkup:
    """Кнопка отмены ввода нового значения параметра."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="« Отмена", callback_data=f"inst:settings:{instance_id}"
                )
            ]
        ]
    )


def instance_cfg_menu(instance_id: int) -> InlineKeyboardMarkup:
    """Подменю «Конфиги»: загрузить/посмотреть _main / auto_response / auto_delivery."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Текущий _main.cfg",
                    callback_data=f"inst:cfg:show:{instance_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Загрузить _main.cfg",
                    callback_data=f"inst:cfg:main:{instance_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Загрузить auto_response.cfg",
                    callback_data=f"inst:cfg:resp:{instance_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Загрузить auto_delivery.cfg",
                    callback_data=f"inst:cfg:deliv:{instance_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="« Назад",
                    callback_data=f"inst:open:{instance_id}",
                )
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Admin keyboards
# ---------------------------------------------------------------------------


def admin_menu() -> InlineKeyboardMarkup:
    """Главное меню админа — только то, что реально нужно."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Статистика", callback_data="admin:stats")],
            [InlineKeyboardButton(text="Серверы (все)", callback_data="admin:servers")],
            [InlineKeyboardButton(text="Хосты", callback_data="admin:hosts")],
            [InlineKeyboardButton(text="Юзер по id", callback_data="admin:user")],
            [InlineKeyboardButton(text="Купоны", callback_data="admin:coupons")],
            [InlineKeyboardButton(text="Рассылка", callback_data="admin:broadcast")],
            [InlineKeyboardButton(text="« В меню", callback_data="menu")],
        ]
    )


def admin_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="« В админку", callback_data="admin")]]
    )


def admin_user_actions(user_id: int) -> InlineKeyboardMarkup:
    """Действия над юзером после поиска по id."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Выдать 30 дней",
                    callback_data=f"admin:user:grant:{user_id}:30",
                ),
                InlineKeyboardButton(
                    text="Выдать 7 дней",
                    callback_data=f"admin:user:grant:{user_id}:7",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Снять подписку",
                    callback_data=f"admin:user:revoke:{user_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Заблокировать",
                    callback_data=f"admin:user:ban:{user_id}",
                ),
                InlineKeyboardButton(
                    text="Разблокировать",
                    callback_data=f"admin:user:unban:{user_id}",
                ),
            ],
            [InlineKeyboardButton(text="« В админку", callback_data="admin")],
        ]
    )


def admin_server_actions(instance_id: int) -> InlineKeyboardMarkup:
    """Админ-действия над любым сервером."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Перезапустить",
                    callback_data=f"adm:srv:restart:{instance_id}",
                ),
                InlineKeyboardButton(
                    text="Остановить",
                    callback_data=f"adm:srv:stop:{instance_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Логи",
                    callback_data=f"adm:srv:logs:{instance_id}",
                ),
                InlineKeyboardButton(
                    text="Удалить",
                    callback_data=f"adm:srv:delete:{instance_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="« Назад к списку",
                    callback_data="admin:servers",
                )
            ],
        ]
    )


def admin_coupons_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Создать купон", callback_data="admin:coupon:new")],
            [InlineKeyboardButton(text="Список купонов", callback_data="admin:coupon:list")],
            [InlineKeyboardButton(text="Удалить купон", callback_data="admin:coupon:del")],
            [InlineKeyboardButton(text="« В админку", callback_data="admin")],
        ]
    )


def admin_coupon_days() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    presets = (7, 30, 90, 365)
    row: list[InlineKeyboardButton] = []
    for d in presets:
        row.append(
            InlineKeyboardButton(text=f"{d} дн", callback_data=f"admin:coupon:days:{d}")
        )
    rows.append(row)
    rows.append([
        InlineKeyboardButton(text="Своё число дней", callback_data="admin:coupon:days:custom")
    ])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="admin:coupons")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_coupon_uses() -> InlineKeyboardMarkup:
    """Step 2 of coupon creation — pick max activations."""
    rows: list[list[InlineKeyboardButton]] = []
    presets = (1, 5, 10, 50)
    row: list[InlineKeyboardButton] = []
    for n in presets:
        label = "1 (одноразовый)" if n == 1 else f"{n}"
        row.append(
            InlineKeyboardButton(text=label, callback_data=f"admin:coupon:uses:{n}")
        )
    rows.append(row[:2])
    rows.append(row[2:])
    rows.append([
        InlineKeyboardButton(text="Своё число", callback_data="admin:coupon:uses:custom")
    ])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="admin:coupon:new")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_confirm(yes_data: str, no_data: str = "admin") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data=yes_data),
                InlineKeyboardButton(text="Нет", callback_data=no_data),
            ]
        ]
    )
