import asyncio
import logging
import os
import re
import time
from urllib.parse import parse_qs

from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from telethon import utils as tg_utils


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
# TELEGRAM BUTTON HELPERS
# ============================================================

def buttons_of(msg):
    """
    Extract every raw Telegram button from reply_markup.
    """

    markup = getattr(msg, "reply_markup", None)

    if not markup:
        return []

    result = []

    for row in getattr(markup, "rows", []) or []:
        for button in getattr(row, "buttons", []) or []:
            result.append(button)

    return result


def debug_buttons(msg):
    """
    Log the exact Telegram button class.

    This is extremely useful when a Mini App isn't detected.
    """

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
# TEXT / HIDDEN URL EXTRACTION
# ============================================================

def urls_of(msg):
    """
    Extract URLs from:
      - URL buttons
      - hidden Telegram text URLs
      - plain text URLs
    """

    urls = []

    # --------------------------------------------------------
    # URL buttons
    # --------------------------------------------------------

    for button in buttons_of(msg):

        kind = type(button).__name__

        if kind in (
            "KeyboardButtonUrl",
            "KeyboardButtonUrlAuth",
        ):
            url = getattr(button, "url", None)

            if url:
                urls.append(url)

    # --------------------------------------------------------
    # Telegram message entities
    # --------------------------------------------------------

    try:

        for entity, text in msg.get_entities_text():

            kind = type(entity).__name__

            if kind == "MessageEntityTextUrl":

                if entity.url:
                    urls.append(entity.url)

            elif kind == "MessageEntityUrl":

                if text:

                    if text.startswith(("http://", "https://")):
                        urls.append(text)

                    else:
                        urls.append("https://" + text)

    except Exception as exc:

        log.debug(
            "Entity URL extraction failed: %s",
            exc,
        )

    # --------------------------------------------------------
    # Plain text URLs
    # --------------------------------------------------------

    text = msg.raw_text or ""

    urls.extend(
        re.findall(
            r"https?://[^\s<>\]\)]+",
            text,
        )
    )

    # --------------------------------------------------------
    # Deduplicate
    # --------------------------------------------------------

    result = []

    for url in urls:

        url = url.strip().rstrip(".,;")

        if url and url not in result:
            result.append(url)

    return result


# ============================================================
# TELEGRAM MINI APP / T.ME LINK RESOLUTION
# ============================================================

TME_RE = re.compile(
    r"^https?://(?:www\.)?(?:t|telegram)\.me/"
    r"([A-Za-z0-9_]+)"
    r"(?:/([A-Za-z0-9_]+))?"
    r"/?(?:\?(.*))?$"
)


async def resolve_url(client, url):
    """
    Resolve Telegram Mini App deep links.

    Normal HTTP URLs are returned unchanged.
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

        # ----------------------------------------------------
        # /bot/app
        # ----------------------------------------------------

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

            bot_id = tg_utils.get_input_user(peer)

            result = await client(
                functions.messages.RequestAppWebViewRequest(
                    peer=peer,

                    app=types.InputBotAppShortName(
                        bot_id=bot_id,
                        short_name=app,
                    ),

                    platform="android",

                    write_allowed=True,

                    start_param=startapp,
                )
            )

            log.info(
                "Resolved Mini App deep link: %s",
                result.url,
            )

            return result.url

        # ----------------------------------------------------
        # /bot?startapp=
        # ----------------------------------------------------

        if startapp is not None and not app:

            peer = await client.get_input_entity(name)

            result = await client(
                functions.messages.RequestMainWebViewRequest(
                    peer=peer,

                    bot=tg_utils.get_input_user(peer),

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
    """
    Convert a Telegram WebView button into its actual WebView URL.
    """

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

        # ----------------------------------------------------
        # KeyboardButtonWebView
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # KeyboardButtonSimpleWebView
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Normal URL button
        # ----------------------------------------------------

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
    """
    Press a Telegram callback button and inspect the callback answer.

    A bot can return a Mini App URL from the callback answer rather
    than placing the URL directly inside the keyboard.
    """

    bot = await client.get_input_entity(BOT)

    try:

        result = await client(
            functions.messages.GetBotCallbackAnswerRequest(
                peer=bot,

                msg_id=msg.id,

                data=button.data,
            )
        )

        log.info(
            "Callback response type=%s",
            type(result).__name__,
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

        # Some responses may expose an update/message-like
        # structure containing additional information.
        log.debug(
            "Callback response: %r",
            result,
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
# NORMAL KEYBOARD BUTTON
# ============================================================

async def normal_button(client, button):

    text = getattr(
        button,
        "text",
        None,
    )

    if not text:
        return None

    log.info(
        "Sending normal keyboard button: %r",
        text,
    )

    try:

        await client.send_message(
            BOT,
            text,
        )

    except Exception as exc:

        log.warning(
            "Normal button failed: %s",
            exc,
        )

    return None


# ============================================================
# FIND MINI APP
# ============================================================

async def find_url(client, msg):
    """
    Complete button discovery pipeline.
    """

    log.info(
        "Inspecting message id=%s text=%r",
        msg.id,
        (msg.raw_text or "")[:150],
    )

    # VERY IMPORTANT:
    # First print exactly what Telegram sent.
    debug_buttons(msg)

    buttons = buttons_of(msg)

    # ========================================================
    # 1. MINI APP / WEBVIEW BUTTONS
    # ========================================================

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

                log.info(
                    "Mini App WebView URL obtained."
                )

                return url

    # ========================================================
    # 2. NORMAL URL BUTTON
    # ========================================================

    for button in buttons:

        kind = type(button).__name__

        if kind in (
            "KeyboardButtonUrl",
            "KeyboardButtonUrlAuth",
        ):

            text = (
                getattr(button, "text", "")
                or ""
            )

            if BUTTON_TEXT:

                if BUTTON_TEXT.lower() not in text.lower():
                    continue

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

    # ========================================================
    # 3. CALLBACK BUTTON
    # ========================================================

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

    # ========================================================
    # 4. TEXT / HIDDEN URL
    # ========================================================

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

    log.info(
        "Sending: %r",
        TEXT,
    )

    sent = await client.send_message(
        BOT,
        TEXT,
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

            # Ignore our own messages.
            if msg.out:
                continue

            # Avoid repeatedly processing
            # the exact same message.
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
        "No Mini App URL found within %.1f seconds.",
        RESPONSE_TIMEOUT,
    )

    return None


# ============================================================
# OPTIONAL PLAYWRIGHT
# ============================================================

async def open_webview(browser, url):

    context = await browser.new_context(
        viewport={
            "width": 390,
            "height": 844,
        },

        is_mobile=True,

        has_touch=True,
    )

    page = await context.new_page()

    try:

        log.info(
            "Opening WebView..."
        )

        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60_000,
        )

        log.info(
            "WebView loaded: %s",
            page.url,
        )

        if PAGE_CLICK_TEXT:

            try:

                locator = page.get_by_text(
                    PAGE_CLICK_TEXT,
                    exact=False,
                ).first

                await locator.click(
                    timeout=15_000,
                )

                log.info(
                    "Clicked page element: %r",
                    PAGE_CLICK_TEXT,
                )

            except Exception as exc:

                log.warning(
                    "Page click failed: %s",
                    exc,
                )

        await page.wait_for_timeout(
            3_000
        )

    finally:

        await context.close()


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

    # --------------------------------------------------------
    # Session
    # --------------------------------------------------------

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

    browser = None
    playwright = None

    try:

        log.info(
            "Connecting to Telegram..."
        )

        await client.start()

        log.info(
            "Telegram connection established."
        )

        # ----------------------------------------------------
        # Optional Playwright
        # ----------------------------------------------------

        if (
            HEADLESS
            and PAGE_CLICK_TEXT
        ):

            try:

                from playwright.async_api import (
                    async_playwright,
                )

                playwright = (
                    await async_playwright().start()
                )

                browser = await playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                )

            except ImportError:

                log.warning(
                    "Playwright is not installed."
                )

        # ----------------------------------------------------
        # Main loop
        # ----------------------------------------------------

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

                if not url:

                    log.warning(
                        "No usable Mini App URL found."
                    )

                else:

                    log.info(
                        "FOUND URL: %s",
                        url,
                    )

                    if browser:

                        await open_webview(
                            browser,
                            url,
                        )

   
