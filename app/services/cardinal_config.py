"""Default Mi Host config for FunPay Cardinal tenants.

Cardinal expects ``configs/_main.cfg`` to exist before ``main.py`` runs;
otherwise it drops into an interactive ``first_setup()`` (input prompts),
which obviously cannot work inside a managed subprocess.

We pre-generate a fully-valid ``_main.cfg`` with friendly Mi Host defaults
(autoRaise + autoResponse on, cute greeting / order-confirm / 5-star
review-reply texts pre-filled) so a buyer gets a working shop the moment
they paste their golden_key. Everything is overridable via the
``Залить _main.cfg`` button.

Format quirks (must match Cardinal's ``configparser.ConfigParser`` setup
in ``Utils/config_loader.py::create_config_obj``):
* delimiter is ``:`` not ``=``
* ``optionxform = str`` — keys are case-sensitive
* ``interpolation=None`` — no ``%`` parsing
* multi-line values are supported via continuation lines indented with
 whitespace; ConfigParser handles serialization+reparse round-trip.
"""
from __future__ import annotations

from configparser import ConfigParser
from io import StringIO
from typing import Optional

# ``hash_password`` from Cardinal/Utils/cardinal_tools.py uses bcrypt; we pin
# a stable placeholder hash here so the file validates even before the user
# enables Telegram. The hash is never actually used while Telegram.enabled=0.
_PLACEHOLDER_BCRYPT_HASH = "$2b$12$abcdefghijklmnopqrstuv1234567890ABCDEFGHIJKL/MNOPQRSTU"

# A real-looking Chrome UA so FunPay doesn't flag us as a bot. Cardinal's own
# first_setup() suggests the same string.
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/109.0.0.0 Safari/537.36"
)

# --- Friendly defaults (cat-themed shop) -------------------------------------
# The user supplied an example _main.cfg from a real shop; we adopt its texts
# as Mi Host's out-of-the-box defaults. New buyers get a working autoresponder
# without having to write a single line of config.

_DEFAULT_GREETINGS_TEXT = (
    "Мяу, $username \n"
    "\n"
    "Добро пожаловать в лавку!\n"
    "Сегодня $date_text — отличный день для покупки! \n"
    "\n"
    "Кот уже греется на солнышке и ждёт твой заказ~\n"
    "Пиши или оплати, что нужно, и пушистый продавец мигом примчится! \n"
    "\n"
    "Быстрая (или же автоматическая) выдача\n"
    "Честные цены\n"
    "Безопасная сделка\n"
    "\n"
    "Мррр... не стесняйся, кот добрый! "
)

_DEFAULT_ORDER_CONFIRM_REPLY = (
    "Мяу, $username! \n"
    "\n"
    "Надеюсь, товар понравился и кот не подвёл~ \n"
    "Если всё хорошо — оставь, пожалуйста, отзыв!\n"
    "\n"
    "Каждый отзыв очень важен для пушистого продавца!\n"
    "\n"
    "Спасибо, что выбрал кота! До новых встреч~ \n"
    "$date"
)

_DEFAULT_STAR1_REPLY = (
    "Мяу... $username\n"
    "\n"
    "Кот очень огорчён и просит прощения!\n"
    "Напиши пожалуйста что случилось —\n"
    "кот разберётся и всё решит! \n"
    "\n"
    "ID чата: $chat_id"
)

_DEFAULT_STAR2_REPLY = (
    "Мяу... $username \n"
    "\n"
    "Кот получил отзыв и очень переживает~\n"
    "Пожалуйста, напиши в чём проблема —\n"
    "кот всё исправит! \n"
    "\n"
    "ID чата: $chat_id"
)

_DEFAULT_STAR3_REPLY = (
    "Мяу, $username... \n"
    "\n"
    "Кот немного расстроен, но не обижается~\n"
    "Напиши, что пошло не так —\n"
    "пушистый продавец хочет стать лучше! "
)

_DEFAULT_STAR4_REPLY = (
    "Мррр~ $username, спасибо за отзыв! \n"
    "\n"
    "Кот старался изо всех лап! \n"
    "Если что-то было не так — напиши,\n"
    "кот обязательно исправится~ "
)

_DEFAULT_STAR5_REPLY = (
    "МЯУ! $username, спасибо огромное!\n"
    "\n"
    "5 звёздочек — кот счастлив и мурчит\n"
    "на максимальной громкости! \n"
    "\n"
    "Приходи ещё — пушистый продавец\n"
    "всегда рад! "
)


def default_main_cfg(
    *,
    golden_key: str,
    user_agent: str = "",
    telegram_token: str = "",
    telegram_enabled: bool = False,
    secret_key_hash: str = _PLACEHOLDER_BCRYPT_HASH,
    locale: str = "ru",
    proxy: str = "",
    auto_raise: bool = True,
    auto_response: bool = True,
    auto_delivery: bool = False,
    multi_delivery: bool = False,
    auto_restore: bool = False,
    auto_disable: bool = False,
) -> dict[str, dict[str, str]]:
    """Build the dict that maps 1:1 to ``configs/_main.cfg`` sections."""
    b = lambda v: "1" if v else "0"
    ua = user_agent or _DEFAULT_USER_AGENT
    return {
        "FunPay": {
            "golden_key": golden_key,
            "user_agent": ua,
            "autoRaise": b(auto_raise),
            "autoResponse": b(auto_response),
            "autoDelivery": b(auto_delivery),
            "multiDelivery": b(multi_delivery),
            "autoRestore": b(auto_restore),
            "autoDisable": b(auto_disable),
            "oldMsgGetMode": "0",
            "locale": locale,
            "keepSentMessagesUnread": "0",
        },
        "Telegram": {
            "enabled": b(telegram_enabled),
            "token": telegram_token,
            "secretKeyHash": secret_key_hash,
            "blockLogin": "0",
            "proxy": "",
        },
        "BlockList": {
            "blockDelivery": "0",
            "blockResponse": "0",
            "blockNewMessageNotification": "0",
            "blockNewOrderNotification": "0",
            "blockCommandNotification": "0",
        },
        "NewMessageView": {
            "includeMyMessages": "1",
            "includeFPMessages": "1",
            "includeBotMessages": "0",
            "notifyOnlyMyMessages": "0",
            "notifyOnlyFPMessages": "0",
            "notifyOnlyBotMessages": "0",
            "showImageName": "1",
        },
        "Greetings": {
            "ignoreSystemMessages": "1",
            "onlyNewChats": "1",
            "sendGreetings": "1",
            "greetingsText": _DEFAULT_GREETINGS_TEXT,
            "greetingsCooldown": "2",
        },
        "OrderConfirm": {
            "watermark": "0",
            "sendReply": "1",
            "replyText": _DEFAULT_ORDER_CONFIRM_REPLY,
        },
        "ReviewReply": {
            "star1Reply": "1",
            "star2Reply": "1",
            "star3Reply": "1",
            "star4Reply": "1",
            "star5Reply": "1",
            "star1ReplyText": _DEFAULT_STAR1_REPLY,
            "star2ReplyText": _DEFAULT_STAR2_REPLY,
            "star3ReplyText": _DEFAULT_STAR3_REPLY,
            "star4ReplyText": _DEFAULT_STAR4_REPLY,
            "star5ReplyText": _DEFAULT_STAR5_REPLY,
        },
        "Proxy": {
            "enable": "1" if proxy else "0",
            "proxy": proxy,
            "check": "0",
        },
        "Other": {
            "watermark": "",
            "requestsDelay": "4",
            "language": locale,
        },
    }


def render_main_cfg(sections: dict[str, dict[str, str]]) -> str:
    """Serialize the section dict to Cardinal's ``:`` -delimited INI."""
    cp = ConfigParser(delimiters=(":",), interpolation=None)
    cp.optionxform = str  # type: ignore[assignment]
    for sect, kv in sections.items():
        cp[sect] = kv
    buf = StringIO()
    cp.write(buf, space_around_delimiters=True)
    return buf.getvalue()


def merge_overrides(
    base: dict[str, dict[str, str]],
    overrides: Optional[dict[str, dict[str, str]]],
) -> dict[str, dict[str, str]]:
    """Deep-merge user overrides over the default config."""
    if not overrides:
        return base
    out = {k: dict(v) for k, v in base.items()}
    for sect, kv in overrides.items():
        if not isinstance(kv, dict):
            continue
        out.setdefault(sect, {})
        for k, v in kv.items():
            out[sect][k] = str(v)
    return out


TG_TOKEN_RE = __import__("re").compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")

_PROXY_RE = __import__("re").compile(
    r"^(?:(?P<scheme>socks5h?|socks4|http|https)://)?"
    r"(?:(?P<login>[^:@\s]+):(?P<password>[^@\s]*)@)?"
    r"(?P<host>[A-Za-z0-9\.\-]+):(?P<port>\d{2,5})$"
)


def validate_tg_token(token: str) -> bool:
    return bool(TG_TOKEN_RE.match(token.strip()))


def validate_proxy(proxy: str) -> tuple[bool, str]:
    """Returns (ok, normalized) where normalized is the canonical form
 Cardinal stores in ``_main.cfg`` (scheme://login:password@host:port)."""
    s = proxy.strip()
    if not s:
        return False, ""
    m = _PROXY_RE.match(s)
    if not m:
        return False, ""
    scheme = m.group("scheme") or "http"
    host = m.group("host")
    port = m.group("port")
    login = m.group("login")
    password = m.group("password") or ""
    if login:
        return True, f"{scheme}://{login}:{password}@{host}:{port}"
    return True, f"{scheme}://{host}:{port}"


def validate_password(pw: str) -> tuple[bool, str]:
    """Cardinal's own policy: ≥8 chars, mixed case, at least one digit."""
    if len(pw) < 8:
        return False, "Пароль должен быть ≥ 8 символов."
    if pw.lower() == pw or pw.upper() == pw:
        return False, "В пароле нужны и заглавные, и строчные буквы."
    if not any(c.isdigit() for c in pw):
        return False, "В пароле нужна хотя бы одна цифра."
    return True, ""


def hash_password(pw: str) -> str:
    """Same hash format Cardinal expects (``bcrypt``)."""
    import bcrypt

    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def generate_password(length: int = 12) -> str:
    """Generate a Cardinal-policy-compliant password (mixed case + digit)."""
    import secrets
    import string

    # Guaranteed one of each category.
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        ok, _ = validate_password(pw)
        if ok:
            return pw


__all__ = (
    "default_main_cfg",
    "render_main_cfg",
    "merge_overrides",
    "validate_tg_token",
    "validate_proxy",
    "validate_password",
    "hash_password",
    "generate_password",
)
