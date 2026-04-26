"""Default Mi Host config for FunPay Cardinal tenants.

Cardinal expects ``configs/_main.cfg`` to exist before ``main.py`` runs;
otherwise it drops into an interactive ``first_setup()`` (input prompts),
which obviously cannot work inside a managed subprocess.

We pre-generate a fully-valid ``_main.cfg`` with sane defaults (Telegram
disabled, locale=ru, all features off) and let the user later toggle
sections by uploading their own ``_main.cfg`` via the bot.

Format quirks (must match Cardinal's ``configparser.ConfigParser`` setup
in ``Utils/config_loader.py::create_config_obj``):
* delimiter is ``:`` not ``=``
* ``optionxform = str`` — keys are case-sensitive
* ``interpolation=None`` — no ``%`` parsing
"""
from __future__ import annotations

from configparser import ConfigParser
from io import StringIO
from typing import Iterable, Optional

# ``hash_password`` from Cardinal/Utils/cardinal_tools.py uses bcrypt; we pin
# a stable placeholder hash here so the file validates even before the user
# enables Telegram. The hash is never actually used while Telegram.enabled=0.
_PLACEHOLDER_BCRYPT_HASH = "$2b$12$abcdefghijklmnopqrstuv1234567890ABCDEFGHIJKL/MNOPQRSTU"


def default_main_cfg(
    *,
    golden_key: str,
    user_agent: str = "",
    telegram_token: str = "",
    telegram_enabled: bool = False,
    secret_key_hash: str = _PLACEHOLDER_BCRYPT_HASH,
    locale: str = "ru",
    proxy: str = "",
    auto_raise: bool = False,
    auto_response: bool = False,
    auto_delivery: bool = False,
    multi_delivery: bool = False,
    auto_restore: bool = False,
    auto_disable: bool = False,
) -> dict[str, dict[str, str]]:
    """Build the dict that maps 1:1 to ``configs/_main.cfg`` sections."""
    b = lambda v: "1" if v else "0"
    return {
        "FunPay": {
            "golden_key": golden_key,
            "user_agent": user_agent,
            "autoRaise": b(auto_raise),
            "autoResponse": b(auto_response),
            "autoDelivery": b(auto_delivery),
            "multiDelivery": b(multi_delivery),
            "autoRestore": b(auto_restore),
            "autoDisable": b(auto_disable),
            "oldMsgGetMode": "0",
            "keepSentMessagesUnread": "0",
            "locale": locale,
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
            "ignoreSystemMessages": "0",
            "onlyNewChats": "0",
            "sendGreetings": "0",
            "greetingsText": "Привет, $chat_name!",
            "greetingsCooldown": "2",
        },
        "OrderConfirm": {
            "watermark": "1",
            "sendReply": "0",
            "replyText": (
                "$username, спасибо за подтверждение заказа $order_id! "
                "Если не сложно, оставь, пожалуйста, отзыв!"
            ),
        },
        "ReviewReply": {
            "star1Reply": "0",
            "star2Reply": "0",
            "star3Reply": "0",
            "star4Reply": "0",
            "star5Reply": "0",
            "star1ReplyText": "",
            "star2ReplyText": "",
            "star3ReplyText": "",
            "star4ReplyText": "",
            "star5ReplyText": "",
        },
        "Proxy": {
            "enable": "1" if proxy else "0",
            "proxy": proxy,
            "check": "0",
        },
        "Other": {
            "watermark": "🐦",
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


__all__ = ("default_main_cfg", "render_main_cfg", "merge_overrides")
