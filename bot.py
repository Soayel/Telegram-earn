import asyncio
import logging
import os
import re
import time
from urllib.parse import parse_qs

from telethon import TelegramClient, functions, types, utils as tg_utils
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger("telegram-miniapp")


# ============================================================
# CONFIG
# ============================================================

def cfg(name, default=""):
    value = os.environ.get(name)
    return default if value is None else value


API_ID = int(cfg("API_ID", "0"))
API_HASH = cfg("API_HASH", "")
SESSION = cfg("SESSION", "")

BOT = cfg("BOT", "@your_bot")
TEXT = cfg("TEXT", "your text")

BUTTON_TEXT = cfg("BUTTON_TEXT", "")

MAX_ROUNDS = int(cfg("MAX_ROUNDS", "10"))
ROUND_DELAY = float(cfg("ROUND_DELAY", "5"))
RESPONSE_TIMEOUT = float(cfg("RESPONSE_TIMEOUT", "40"))

PLATFORM = cfg("PLATFORM", "android")


# ============================================================
# BUTTON HELPERS
# ============================================================

def buttons_of(message):
    markup = getattr(message, "reply_markup", None)

    if not markup:
        return []

    result = []

    for row in getattr(markup, "rows", []) or []:
        result.extend(
            getattr(row, "buttons", []) or []
        )

    return result


def button_text(button):
    return (
        getattr(button, "text", None)
        or ""
    ).strip()


def button_matches(button):
    if not BUTTON_TEXT:
        return True

    return BUTTON_TEXT.casefold() in (
        button_text(button).casefold()
    )


def debug_buttons(message):
    buttons = buttons_of(message)

    if not buttons:
        log.info(
            "Message %s contains NO buttons",
            message.id,
        )
        return

    for index, button in enumerate(buttons):
        log.info(
            "BUTTON[%d] | type=%s | text=%r | url=%r | data=%r",
            index,
            type(button).__name__,
            getattr(button, "text", None),
            getattr(button, "url", None),
            getattr(button, "data", None),
        )


# ============================================================
# URL EXTRACTION
# ============================================================

URL_RE = re.compile(
    r"https?://[^\s<>\]\)]+",
    re.IGNORECASE,
)


def urls_of(message):
    urls = []

    def add_url(url):
        if not url:
            return

        url = url.strip().rstrip(".,;")

        if url and url not in urls:
            urls.append(url)

    # --------------------------------------------------------
    # URLs contained in buttons
    # --------------------------------------------------------

    for button in buttons_of(message):
        kind = type(button).__name__

        if kind in {
            "KeyboardButtonUrl",
            "KeyboardButtonUrlAuth",
            "KeyboardButtonWebView",
            "KeyboardButtonSimpleWebView",
        }:
            add_url(
                getattr(button, "url", None)
            )

    # --------------------------------------------------------
    # Message entities
    # --------------------------------------------------------

    try:
        for entity, text in message.get_entities_text():

            kind = type(entity).__name__

            if kind == "MessageEntityTextUrl":
                add_url(
                    getattr(entity, "url", None)
                )

            elif kind == "MessageEntityUrl":
                if text:
                    add_url(text)

    except Exception as exc:
        log.debug(
            "Entity URL extraction failed: %s",
            exc,
        )

    # --------------------------------------------------------
    # Raw text URLs
    # --------------------------------------------------------

    for url in URL_RE.findall(
        message.raw_text or ""
    ):
        add_url(url)

    return urls


# ============================================================
# TELEGRAM MINI APP DEEP LINKS
# ============================================================

TME_RE = re.compile(
    r"^https?://"
    r"(?:www\.)?"
    r"(?:t|telegram)\.me/"
    r"([^/?#]+)"
    r"(?:/([^/?#]+))?"
    r"(?:\?(.*))?$",
    re.IGNORECASE,
)


IGNORED_PATHS = {
    "c",
    "joinchat",
    "addstickers",
    "addemoji",
    "share",
    "proxy",
    "socks",
}


async def resolve_url(client, url):
    """
    Resolve Telegram deep links into the actual Mini App
    WebView URL where possible.

    Ordinary URLs are returned unchanged.
    """

    match = TME_RE.match(url)

    if not match:
        return url

    name, app, query = match.groups()

    params = parse_qs(
        query or "",
        keep_blank_values=True,
    )

    startapp = params.get(
        "startapp",
        [None],
    )[0]

    try:

        # ====================================================
        # /bot/app
        # ====================================================

        if (
            app
            and not app.isdigit()
            and name.casefold() not in IGNORED_PATHS
        ):

            peer = await client.get_input_entity(
                name
            )

            result = await client(
                functions.messages.RequestAppWebViewRequest(
                    peer=peer,
                    app=types.InputBotAppShortName(
                        bot_id=tg_utils.get_input_user(
                            peer
                        ),
                        short_name=app,
                    ),
                    platform=PLATFORM,
                    write_allowed=True,
                    start_param=startapp,
                )
            )

            resolved = getattr(
                result,
                "url",
                None,
            )

            if resolved:
                log.info(
                    "Resolved Mini App URL: %s",
                    resolved,
                )

                return resolved

        # ====================================================
        # /bot?startapp=...
        # ====================================================

        if startapp is not None and not app:

            peer = await client.get_input_entity(
                name
            )

            result = await client(
                functions.messages.RequestMainWebViewRequest(
                    peer=peer,
                    bot=tg_utils.get_input_user(
                        peer
                    ),
                    platform=PLATFORM,
                    start_param=startapp or None,
                )
            )

            resolved = getattr(
                result,
                "url",
                None,
            )

            if resolved:
                log.info(
                    "Resolved main Mini App URL: %s",
                    resolved,
                )

                return resolved

    except FloodWaitError:
        raise

    except RPCError as exc:
        log.warning(
            "Telegram RPC error resolving URL: %s",
            exc,
        )

    except Exception:
        log.exception(
            "Mini App deep-link resolution failed"
        )

    return url


# ============================================================
# WEBVIEW BUTTON
# ============================================================

async def webview_url(client, button):
    bot = await client.get_input_entity(BOT)

    theme = types.DataJSON(
        data=(
            '{"bg_color":"#ffffff",'
            '"text_color":"#000000",'
            '"button_color":"#3390ec",'
            '"button_text_color":"#ffffff"}'
        )
    )

    kind = type(button).__name__

    log.info(
        "Processing button type=%s text=%r",
        kind,
        button_text(button),
    )

    try:

        # ----------------------------------------------------
        # KeyboardButtonWebView
        # ----------------------------------------------------

        if kind == "KeyboardButtonWebView":

            result = await client(
                functions.messages.RequestWebViewRequest(
                    peer=bot,
                    bot=bot,
                    platform=PLATFORM,
                    url=button.url,
                    theme_params=theme,
                )
            )

            return getattr(
                result,
                "url",
                None,
            )

        # ----------------------------------------------------
        # KeyboardButtonSimpleWebView
        # ----------------------------------------------------

        if kind == "KeyboardButtonSimpleWebView":

            result = await client(
                functions.messages.RequestSimpleWebViewRequest(
                    bot=bot,
                    platform=PLATFORM,
                    url=button.url,
                    theme_params=theme,
                )
            )

            return getattr(
                result,
                "url",
                None,
            )

        # ----------------------------------------------------
        # Normal URL button
        # ----------------------------------------------------

        if kind in {
            "KeyboardButtonUrl",
            "KeyboardButtonUrlAuth",
        }:

            return getattr(
                button,
                "url",
                None,
            )

    except FloodWaitError:
        raise

    except RPCError as exc:
        log.warning(
            "Telegram WebView RPC error: %s",
            exc,
        )

    except Exception:
        log.exception(
            "WebView resolution failed"
        )

    return None


# ============================================================
# CALLBACK DEBUG / EXECUTION
# ============================================================

async def inspect_callback(
    client,
    message,
    button,
):
    """
    Execute an inline callback.

    This is intentionally independent of button.url because
    callback buttons generally store their payload in .data.
    """

    log.info(
        "========== CALLBACK DEBUG =========="
    )

    log.info(
        "Message ID : %s",
        message.id,
    )

    log.info(
        "Message peer: %r",
        message.peer_id,
    )

    log.info(
        "Button type: %s",
        type(button).__name__,
    )

    log.info(
        "Button text: %r",
        getattr(button, "text", None),
    )

    log.info(
        "Button data: %r",
        getattr(button, "data", None),
    )

    log.info(
        "Button URL : %r",
        getattr(button, "url", None),
    )

    data = getattr(
        button,
        "data",
        None,
    )

    if not data:
        log.warning(
            "Callback button contains NO callback data."
        )

        log.info(
            "==================================="
        )

        return None

    try:

        log.info(
            "Sending callback to Telegram..."
        )

        result = await client(
            functions.messages.GetBotCallbackAnswerRequest(
                peer=message.peer_id,
                msg_id=message.id,
                data=data,
            )
        )

        log.info(
            "Callback result type: %s",
            type(result).__name__,
        )

        log.info(
            "Callback result URL: %r",
            getattr(result, "url", None),
        )

        log.info(
            "Callback result message: %r",
            getattr(result, "message", None),
        )

        log.info(
            "Callback result alert: %r",
            getattr(result, "alert", None),
        )

        log.info(
            "Callback result cache_time: %r",
            getattr(result, "cache_time", None),
        )

        log.info(
            "==================================="
        )

        return getattr(
            result,
            "url",
            None,
        )

    except FloodWaitError:
        raise

    except RPCError as exc:

        log.warning(
            "Callback RPC error: %s",
            exc,
        )

    except Exception:

        log.exception(
            "Callback execution failed"
        )

    log.info(
        "==================================="
    )

    return None


# ============================================================
# CALLBACK PROCESSOR
# ============================================================

async def process_callback(
    client,
    message,
    button,
):
    """
    Execute callback.

    A callback can:

        callback
             |
             +--> direct URL
             |
             +--> edited message
             |
             +--> new message
             |
             +--> new keyboard
             |
             +--> Mini App button

    Therefore a missing direct URL is NOT considered failure.
    """

    log.info(
        "Executing callback button: %r",
        button_text(button),
    )

    url = await inspect_callback(
        client,
        message,
        button,
    )

    if url:

        log.info(
            "Callback directly returned URL: %s",
            url,
        )

        return url

    log.info(
        "Callback returned no direct URL."
    )

    log.info(
        "Waiting for bot-side changes..."
    )

    return None


# ============================================================
# MESSAGE SIGNATURE
# ============================================================

def message_signature(message):
    """
    Generate a signature representing the current visible
    state of a Telegram message.

    This allows us to detect an edited keyboard.
    """

    text = message.raw_text or ""

    button_data = []

    for button in buttons_of(message):

        button_data.append(
            (
                type(button).__name__,
                getattr(
                    button,
                    "text",
                    None,
                ),
                getattr(
                    button,
                    "url",
                    None,
                ),
                repr(
                    getattr(
                        button,
                        "data",
                        None,
                    )
                ),
            )
        )

    return (
        text,
        tuple(button_data),
    )


# ============================================================
# FIND URL
# ============================================================

async def find_url(
    client,
    message,
):
    """
    Inspect a single message for anything that can lead to
    a Mini App / WebView URL.
    """

    debug_buttons(message)

    buttons = buttons_of(message)

    # ========================================================
    # 1. DIRECT WEBVIEW / MINI APP
    # ========================================================

    for button in buttons:

        if not button_matches(button):
            continue

        kind = type(button).__name__

        if kind in {
            "KeyboardButtonWebView",
            "KeyboardButtonSimpleWebView",
        }:

            log.info(
                "Direct Mini App button found: %r",
                button_text(button),
            )

            url = await webview_url(
                client,
                button,
            )

            if url:
                return url

    # ========================================================
    # 2. NORMAL URL BUTTON
    # ========================================================

    for button in buttons:

        if not button_matches(button):
            continue

        kind = type(button).__name__

        if kind in {
            "KeyboardButtonUrl",
            "KeyboardButtonUrlAuth",
        }:

            url = getattr(
                button,
                "url",
                None,
            )

            if url:

                log.info(
                    "Normal URL button found: %s",
                    url,
                )

                return await resolve_url(
                    client,
                    url,
                )

    # ========================================================
    # 3. CALLBACK / INLINE BUTTON
    # ========================================================

    callback_found = False

    for button in buttons:

        if not button_matches(button):
            continue

        kind = type(button).__name__

        if kind not in {
            "KeyboardButtonCallback",
            "KeyboardInlineButton",
        }:
            continue

        callback_found = True

        log.info(
            "Inline callback button found: %r",
            button_text(button),
        )

        url = await process_callback(
            client,
            message,
            button,
        )

        if url:

            return await resolve_url(
                client,
                url,
            )

    # ========================================================
    # 4. HIDDEN / ENTITY / TEXT URL
    # ========================================================

    for url in urls_of(message):

        resolved = await resolve_url(
            client,
            url,
        )

        if resolved:
            return resolved

    if callback_found:
        log.info(
            "Callback was executed but produced no "
            "immediate URL; watcher should inspect "
            "subsequent state."
        )

    return None


# ============================================================
# WATCH FOR NEW / EDITED MESSAGES
# ============================================================

async def watch_for_result(
    client,
    sent_id,
    timeout,
):
    """
    Watch the bot conversation after a callback.

    Detects:

        - new messages
        - edited messages
        - changed keyboards
        - newly-created buttons
        - Mini App buttons
        - URL buttons
        - callback-returned URLs
    """

    deadline = (
        time.monotonic()
        + timeout
    )

    known = {}

    log.info(
        "Watching bot conversation for %.1f seconds...",
        timeout,
    )

    while time.monotonic() < deadline:

        try:

            messages = await client.get_messages(
                BOT,
                limit=50,
            )

        except FloodWaitError:
            raise

        except Exception:

            log.exception(
                "Failed to retrieve bot messages"
            )

            await asyncio.sleep(
                1
            )

            continue

        # ----------------------------------------------------
        # Process oldest -> newest
        # ----------------------------------------------------

        for message in sorted(
            messages,
            key=lambda item: item.id,
        ):

            if message.out:
                continue

            if message.id <= sent_id:
                continue

            signature = message_signature(
                message
            )

            previous = known.get(
                message.id
            )

            # ------------------------------------------------
            # Brand-new message
            # ------------------------------------------------

            if previous is None:

                known[
                    message.id
                ] = signature

                log.info(
                    "NEW BOT MESSAGE id=%s",
                    message.id,
                )

                log.info(
                    "Message text: %r",
                    message.raw_text,
                )

            # -----------
