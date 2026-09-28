import asyncio
import logging
import os
import re
import time
from urllib.parse import parse_qs

from telethon import TelegramClient, functions, types, utils as tg_utils
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError


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

def cfg(name, default):
    return os.environ.get(name, default)


API_ID = int(cfg("API_ID", "0"))
API_HASH = cfg("API_HASH", "")
SESSION = cfg("SESSION", "")

BOT = cfg("BOT", "@your_bot")
TEXT = cfg("TEXT", "your text")

BUTTON_TEXT = cfg("BUTTON_TEXT", "")

MAX_ROUNDS = int(cfg("MAX_ROUNDS", "10"))
ROUND_DELAY = float(cfg("ROUND_DELAY", "10"))
RESPONSE_TIMEOUT = float(cfg("RESPONSE_TIMEOUT", "40"))

HEADLESS = cfg("HEADLESS", "1") == "1"
PAGE_CLICK_TEXT = cfg("PAGE_CLICK_TEXT", "")


# ============================================================
# BUTTON EXTRACTION
# ============================================================

def buttons_of(msg):
    markup = getattr(msg, "reply_markup", None)

    if not markup:
        return []

    result = []

    for row in getattr(markup, "rows", []) or []:
        result.extend(
            getattr(row, "buttons", []) or []
        )

    return result


def debug_buttons(msg):
    buttons = buttons_of(msg)

    if not buttons:
        log.info(
            "Message %s contains NO buttons",
            msg.id,
        )
        return

    for index, button in enumerate(buttons):
        log.info(
            "BUTTON[%d] | type=%s | text=%r | url=%r",
            index,
            type(button).__name__,
            getattr(button, "text", None),
            getattr(button, "url", None),
        )


# ============================================================
# URL EXTRACTION
# ============================================================

def urls_of(msg):
    urls = []

    for button in buttons_of(msg):
        kind = type(button).__name__

        if kind in (
            "KeyboardButtonUrl",
            "KeyboardButtonUrlAuth",
        ):
            url = getattr(button, "url", None)

            if url:
                urls.append(url)

    try:
        for entity, text in msg.get_entities_text():
            kind = type(entity).__name__

            if kind == "MessageEntityTextUrl":
                if entity.url:
                    urls.append(entity.url)

            elif kind == "MessageEntityUrl":
                if text:
                    if text.startswith(
                        ("http://", "https://")
                    ):
                        urls.append(text)
                    else:
                        urls.append(
                            "https://" + text
                        )

    except Exception as exc:
        log.debug(
            "Entity URL extraction failed: %s",
            exc,
        )

    urls.extend(
        re.findall(
            r"https?://[^\s<>\]\)]+",
            msg.raw_text or "",
        )
    )

    result = []

    for url in urls:
        url = url.strip().rstrip(".,;")

        if url and url not in result:
            result.append(url)

    return result


# ============================================================
# TELEGRAM MINI APP DEEP LINKS
# ============================================================

TME_RE = re.compile(
    r"^https?://(?:www\.)?(?:t|telegram)\.me/"
    r"([A-Za-z0-9_]+)"
    r"(?:/([A-Za-z0-9_]+))?"
    r"/?(?:\?(.*))?$"
)


async def resolve_url(client, url):
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
        # /bot/app
        if (
            app
            and not app.isdigit()
            and name.lower() not in {
                "c",
                "joinchat",
                "addstickers",
                "addemoji",
                "share",
                "proxy",
                "socks",
            }
        ):
            peer = await client.get_input_entity(name)

            result = await client(
                functions.messages.RequestAppWebViewRequest(
                    peer=peer,
                    app=types.InputBotAppShortName(
                        bot_id=tg_utils.get_input_user(
                            peer
                        ),
                        short_name=app,
                    ),
                    platform="android",
                    write_allowed=True,
                    start_param=startapp,
                )
            )

            log.info(
                "Resolved Mini App: %s",
                result.url,
            )

            return result.url

        # /bot?startapp=...
        if startapp is not None and not app:
            peer = await client.get_input_entity(name)

            result = await client(
                functions.messages.RequestMainWebViewRequest(
                    peer=peer,
                    bot=tg_utils.get_input_user(
                        peer
                    ),
                    platform="android",
                    start_param=startapp or None,
                )
            )

            log.info(
                "Resolved main Mini App: %s",
                result.url,
            )

            return result.url

    except Exception as exc:
        log.exception(
            "Mini App link resolution failed: %s",
            exc,
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
        getattr(button, "text", None),
    )

    try:
        if kind == "KeyboardButtonWebView":
            result = await client(
                functions.messages.RequestWebViewRequest(
                    peer=bot,
                    bot=bot,
                    platform="android",
                    url=button.url,
                    theme_params=theme,
                )
            )

            return result.url

        if kind == "KeyboardButtonSimpleWebView":
            result = await client(
                functions.messages.RequestSimpleWebViewRequest(
                    bot=bot,
                    platform="android",
                    url=button.url,
                    theme_params=theme,
                )
            )

            return result.url

        if kind in (
            "KeyboardButtonUrl",
            "KeyboardButtonUrlAuth",
        ):
            return getattr(
                button,
                "url",
                None,
            )

    except Exception as exc:
        log.exception(
            "WebView resolution failed: %s",
            exc,
        )

    return None


# ============================================================
# CALLBACK BUTTON
# ============================================================

async def callback_button_url(client, msg, button):
    bot = await client.get_input_entity(BOT)

    try:
        result = await client(
            functions.messages.GetBotCallbackAnswerRequest(
                peer=bot,
                msg_id=msg.id,
                data=button.data,
            )
        )

        url = getattr(
            result,
            "url",
            None,
        )

        if url:
            log.info(
                "Callback returned URL: %s",
                url,
            )

            return url

        log.info(
            "Callback returned no URL."
        )

    except FloodWaitError:
        raise

    except Exception as exc:
        log.exception(
            "Callback button failed: %s",
            exc,
        )

    return None


# ============================================================
# FIND URL
# ============================================================

async def find_url(client, msg):
    debug_buttons(msg)

    buttons = buttons_of(msg)

    # --------------------------------------------------------
    # 1. Mini App / WebView
    # --------------------------------------------------------

    for button in buttons:
        kind = type(button).__name__

        text = (
            getattr(button, "text", "")
            or ""
        )

        if BUTTON_TEXT:
            if BUTTON_TEXT.lower() not in text.lower():
                continue

        if kind in (
            "KeyboardButtonWebView",
            "KeyboardButtonSimpleWebView",
        ):
            log.info(
                "Mini App button found: %r",
                text,
            )

            url = await webview_url(
                client,
                button,
            )

            if url:
                return url

    # --------------------------------------------------------
    # 2. Normal URL button
    # --------------------------------------------------------

    for button in buttons:
        kind = type(button).__name__

        text = (
            getattr(button, "text", "")
            or ""
        )

        if BUTTON_TEXT:
            if BUTTON_TEXT.lower() not in text.lower():
                continue

        if kind in (
            "KeyboardButtonUrl",
            "KeyboardButtonUrlAuth",
        ):
            url = getattr(
                button,
                "url",
                None,
            )

            if url:
                log.info(
                    "URL button found: %s",
                    url,
                )

                return url

    # --------------------------------------------------------
    # 3. Callback button
    # --------------------------------------------------------

    for button in buttons:
        kind = type(button).__name__

        if kind != "KeyboardButtonCallback":
            continue

        text = (
            getattr(button, "text", "")
            or ""
        )

        if BUTTON_TEXT:
            if BUTTON_TEXT.lower() not in text.lower():
                continue

        log.info(
            "Callback button found: %r",
            text,
        )

        url = await callback_button_url(
            client,
            msg,
            button,
        )

        if url:
            return url

    # --------------------------------------------------------
    # 4. Hidden/text URLs
    # --------------------------------------------------------

    for url in urls_of(msg):
        resolved = await resolve_url(
            client,
            url,
        )

        if resolved:
            return resolved

    return None


# ============================================================
# WAIT FOR BOT RESPONSE
# ============================================================

async def get_link(client):
    sent = await client.send_message(
        BOT,
        TEXT,
    )

    log.info(
        "Sent message id=%s",
        sent.id,
    )

    deadline = (
        time.monotonic()
        + RESPONSE_TIMEOUT
    )

    checked = set()

    while time.monotonic() < deadline:
        await asyncio.sleep(1)

        messages = await client.get_messages(
            BOT,
            limit=10,
        )

        for msg in sorted(
            messages,
            key=lambda item: item.id,
        ):
            if msg.out:
                continue

            if msg.id in checked:
                continue

            checked.add(msg.id)

            url = await find_url(
                client,
                msg,
            )

            if url:
                return url

    log.warning(
        "No usable Mini App URL found."
    )

    return None


# ============================================================
# MAIN
# ============================================================

async def main():

    if not API_ID:
        raise RuntimeError(
            "API_ID is missing."
        )

    if not API_HASH:
        raise RuntimeError(
            "API_HASH is missing."
        )

    if not BOT:
        raise RuntimeError(
            "BOT is missing."
        )

    session = (
        StringSession(SESSION)
        if SESSION
        else "telegram_session"
    )

    client = TelegramClient(
        session,
        API_ID,
        API_HASH,
    )

    try:
        log.info(
            "Connecting to Telegram..."
        )

        await client.start()

        log.info(
            "Telegram connection established."
        )

        for round_number in range(
            1,
            MAX_ROUNDS + 1,
        ):
            log.info(
                "========== ROUND %d/%d ==========",
                round_number,
                MAX_ROUNDS,
            )

            try:
                url = await get_link(
                    client
                )

                if url:
                    log.info(
                        "FOUND MINI APP URL: %s",
                        url,
                    )
                else:
                    log.warning(
                        "Mini App URL was not found."
                    )

            except FloodWaitError as exc:
                log.warning(
                    "Telegram FloodWait: %s seconds",
                    exc.seconds,
                )

                await asyncio.sleep(
                    exc.seconds
                )

            except Exception:
                log.exception(
                    "Round failed."
                )

            if round_number < MAX_ROUNDS:
                await asyncio.sleep(
                    ROUND_DELAY
                )

    finally:
        await client.disconnect()

        log.info(
            "Disconnected."
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        log.info(
            "Stopped by user."
    )
