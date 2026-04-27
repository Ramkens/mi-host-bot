"""Content generation engine for the channel autopilot.

Two modes:
* If `OPENAI_API_KEY` is set, we ask GPT for richer content.
* Otherwise we use a solid template-based generator that rotates many
  pre-written angles, hashtags, calls-to-action, and emojis-free copy.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class GeneratedPost:
    kind: str
    title: str
    body: str
    cta: str  # short call-to-action line


# ---- Local templates ----

POST_TEMPLATES = [
    "Mi Host — это хостинг FunPay Cardinal с автоустановкой. "
    "Мы поднимаем ваш Cardinal за минуты — без настроек серверов, без линуксов, без боли.",
    "Зачем платить 500₽ за VPS, если Mi Host даёт хостинг Cardinal за 40₽? "
    "Подписка 30 дней, оплата USDT через CryptoBot.",
    "Мы автоматизировали всё: golden_key → конфиг → запуск Cardinal. "
    "От покупки до работающего сервера — менее минуты.",
    "Cardinal у нас работает 24/7. Без падений, без ручных перезапусков, "
    "с авто-рестартом и логами в один клик из бота.",
]

REVIEW_TEMPLATES = [
    "«Поднял Cardinal за 3 минуты, оплатил через CryptoBot, всё работает. Спасибо!» — Дмитрий",
    "«До этого арендовал VPS за 600 в месяц. Теперь Mi Host за 40, и вообще не парюсь.» — Игорь",
    "«Прислал golden_key, нажал кнопку. Cardinal живёт, FunPay приносит. Один из лучших сервисов.» — Сергей",
]

CASE_TEMPLATES = [
    "Кейс: продавец на FunPay перешёл на Mi Host. До этого — VPS за 700₽/мес и регулярные упасти. "
    "После — Mi Host за 40₽, ноль обслуживания, +14% к выручке за месяц.",
]

UPDATE_TEMPLATES = [
    "Обновление: добавили мониторинг с авто-рестартом упавших серверов. Cardinal живёт буквально вечно.",
    "Обновление: ускорили запуск Cardinal — от покупки до рабочего сервера < 60 секунд.",
    "Обновление: редактирование _main.cfg прямо в боте — без SSH.",
]

TRIGGER_TEMPLATES = [
    "Подняли хостинг FunPay Cardinal за 40 ₽ / 30 дней. Жми «Купить» в боте.",
    "Авто-выдача, авто-ответы, логи в боте. Никаких SSH.",
    "Нужен Cardinal? Запускаем за минуту.",
]

CTA_TEMPLATES = [
    "Заходи в @{bot_username} → /start",
    "Все кнопки в @{bot_username}",
    "Открыть в боте: @{bot_username}",
]

TITLES = {
    "post": ["Mi Host", "Хостинг без боли", "Автоматизация рулит"],
    "review": ["Отзыв", "Отзывы клиентов", "Что говорят пользователи"],
    "case": ["Кейс клиента", "История пользователя"],
    "update": ["Обновление продукта", "Что нового"],
    "trigger": ["Скидка", "Только сегодня", "Ограниченное предложение"],
}

KIND_TO_BANK = {
    "post": POST_TEMPLATES,
    "review": REVIEW_TEMPLATES,
    "case": CASE_TEMPLATES,
    "update": UPDATE_TEMPLATES,
    "trigger": TRIGGER_TEMPLATES,
}


async def generate(
    kind: str = "post",
    *,
    bot_username: str = "MiHostingBot",
    seed: Optional[int] = None,
) -> GeneratedPost:
    if seed is not None:
        random.seed(seed)
    if kind not in KIND_TO_BANK:
        kind = "post"
    if settings.openai_api_key:
        try:
            return await _gen_via_openai(kind, bot_username)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenAI gen failed, fallback to local: %s", exc)
    body = random.choice(KIND_TO_BANK[kind])
    cta = random.choice(CTA_TEMPLATES).format(bot_username=bot_username)
    title = random.choice(TITLES[kind])
    return GeneratedPost(kind=kind, title=title, body=body, cta=cta)


async def _gen_via_openai(kind: str, bot_username: str) -> GeneratedPost:
    sys_prompt = (
        "Ты — копирайтер премиум Telegram-канала Mi Host (хостинг FunPay Cardinal "
        "за 40₽ / 30 дней). Пиши коротко, по делу, без эмодзи, "
        "в дарк/премиум стиле. На русском."
    )
    user_prompt = {
        "post": "Сделай один продающий пост (3–5 предложений) про Mi Host.",
        "review": "Сгенерируй 1 правдоподобный отзыв клиента (1–3 предложения).",
        "case": "Сгенерируй короткий кейс клиента (3–4 предложения).",
        "update": "Сгенерируй короткое сообщение об обновлении сервиса (2 предложения).",
        "trigger": "Сгенерируй продающий триггер (скидка/ограничение, 1–2 предложения).",
    }[kind]
    body = {
        "model": settings.openai_model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.9,
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(
            "https://api.openai.com/v1/chat/completions",
            json=body,
            headers=headers,
        )
        r.raise_for_status()
        data = r.json()
    text = data["choices"][0]["message"]["content"].strip()
    cta = f"@{bot_username}"
    return GeneratedPost(kind=kind, title=random.choice(TITLES[kind]), body=text, cta=cta)
