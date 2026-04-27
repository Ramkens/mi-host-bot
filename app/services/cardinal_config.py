"""Default Mi Host config for FunPay Cardinal tenants.

Cardinal expects ``configs/_main.cfg`` to exist before ``main.py`` runs;
otherwise it drops into an interactive ``first_setup()`` which cannot work
inside a managed subprocess. We pre-generate a fully-valid ``_main.cfg`` so
buyers get a working shop the moment they paste their golden_key.

Format quirks (must match Cardinal's ``configparser.ConfigParser`` setup
in ``Utils/config_loader.py::create_config_obj``):

* delimiter is ``:`` not ``=``
* ``optionxform = str`` — keys are case-sensitive
* ``interpolation=None`` — no ``%`` parsing
* multi-line values are supported via continuation lines indented with
  whitespace; ConfigParser handles serialization+reparse round-trip.

The schema we emit covers every key Cardinal's validator checks
(``Utils/config_loader.py::load_main_config``):

* ``FunPay``: golden_key, user_agent, autoRaise, autoResponse, autoDelivery,
  multiDelivery, autoRestore, autoDisable, oldMsgGetMode,
  keepSentMessagesUnread, locale
* ``Telegram``: enabled, token, secretKeyHash, blockLogin
* ``BlockList``: blockDelivery, blockResponse, blockNewMessageNotification,
  blockNewOrderNotification, blockCommandNotification
* ``NewMessageView``: includeMyMessages, includeFPMessages, includeBotMessages,
  notifyOnlyMyMessages, notifyOnlyFPMessages, notifyOnlyBotMessages,
  showImageName
* ``Greetings``: ignoreSystemMessages, onlyNewChats, sendGreetings,
  greetingsText, greetingsCooldown
* ``OrderConfirm``: watermark, sendReply, replyText
* ``ReviewReply``: star{1..5}Reply, star{1..5}ReplyText
* ``Proxy``: enable, ip, port, login, password, check
* ``Other``: watermark, requestsDelay, language
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

# Plain, emoji-free defaults. Buyers can change anything via the
# «Загрузить _main.cfg» / «Залить auto_response.cfg» buttons.

_DEFAULT_GREETINGS_TEXT = (
    "Привет, $username!\n"
    "Спасибо, что зашёл в магазин.\n"
    "Если что-то нужно — напиши, отвечу как только смогу.\n"
)

_DEFAULT_ORDER_CONFIRM_REPLY = (
    "$username, спасибо за подтверждение заказа $order_id!\n"
    "Если не сложно, оставь, пожалуйста, отзыв."
)

_DEFAULT_STAR_REPLY = (
    "$username, спасибо за отзыв!"
)


def default_main_cfg(
    *,
    golden_key: str,
    user_agent: str = "",
    telegram_token: str = "",
    telegram_enabled: bool = False,
    secret_key_hash: str = _PLACEHOLDER_BCRYPT_HASH,
    locale: str = "ru",
    proxy_ip: str = "",
    proxy_port: str = "",
    proxy_login: str = "",
    proxy_password: str = "",
    auto_raise: bool = True,
    auto_response: bool = True,
    auto_delivery: bool = True,
    multi_delivery: bool = False,
    auto_restore: bool = True,
    auto_disable: bool = True,
    greetings_text: str = "",
    order_confirm_reply: str = "",
    star_reply_text: str = "",
) -> dict[str, dict[str, str]]:
    """Build the dict that maps 1:1 to ``configs/_main.cfg`` sections."""

    def b(v: bool) -> str:
        return "1" if v else "0"

    ua = user_agent or _DEFAULT_USER_AGENT
    proxy_enabled = bool(proxy_ip and proxy_port)
    greet = greetings_text or _DEFAULT_GREETINGS_TEXT
    order_reply = order_confirm_reply or _DEFAULT_ORDER_CONFIRM_REPLY
    star = star_reply_text or _DEFAULT_STAR_REPLY
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
            "keepSentMessagesUnread": "0",
            "locale": locale,
        },
        "Telegram": {
            "enabled": b(telegram_enabled),
            "token": telegram_token,
            "secretKeyHash": secret_key_hash,
            "blockLogin": "0",
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
            "greetingsText": greet,
            "greetingsCooldown": "2",
        },
        "OrderConfirm": {
            "watermark": "0",
            "sendReply": "1",
            "replyText": order_reply,
        },
        "ReviewReply": {
            "star1Reply": "1",
            "star2Reply": "1",
            "star3Reply": "1",
            "star4Reply": "1",
            "star5Reply": "1",
            "star1ReplyText": star,
            "star2ReplyText": star,
            "star3ReplyText": star,
            "star4ReplyText": star,
            "star5ReplyText": star,
        },
        "Proxy": {
            # Cardinal's validator requires ip/port/login/password/check; an
            # `enable` flag is also kept for backward-compat with first_setup.py.
            "enable": b(proxy_enabled),
            "ip": proxy_ip,
            "port": proxy_port,
            "login": proxy_login,
            "password": proxy_password,
            "check": "0",
        },
        "Other": {
            # Watermark prepended to every outgoing FunPay message. Empty by
            # default — buyers can set their own brand via _main.cfg upload.
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


__all__ = ("default_main_cfg", "render_main_cfg", "merge_overrides")
