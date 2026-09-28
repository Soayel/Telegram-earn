import asyncio, os, re, random, logging, time, webbrowser, subprocess
import urllib.request
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from telethon.tl import types, functions

try:
    from playwright.async_api import async_playwright
    HAS_PW = True
except ImportError:          # e.g. Pydroid -> falls back to phone browser
    HAS_PW = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("bot")

# ---------------- SETTINGS (edit here, or use env variables on Railway) ----
def cfg(name, default):
    return os.environ.get(name, default)

API_ID   = int(cfg("API_ID", "0"))          # your api_id
API_HASH = cfg("API_HASH", "")              # your api_hash
SESSION  = cfg("SESSION", "")               # leave empty on Pydroid/PC (asks login once)
BOT      = cfg("BOT", "@your_bot")          # bot username
TEXT     = cfg("TEXT", "your text")         # text to send

MIN_DELAY   = int(cfg("MIN_DELAY", "5"))   # seconds between rounds (random between)
MAX_DELAY   = int(cfg("MAX_DELAY", "15"))
STAY_MIN    = int(cfg("STAY_MIN", "3"))     # seconds to stay on the page
STAY_MAX    = int(cfg("STAY_MAX", "8"))
MAX_PER_DAY = int(cfg("MAX_PER_DAY", "500"))  # daily limit
HEADLESS    = cfg("HEADLESS", "1") == "1"
PAGE_CLICK_TEXT = cfg("PAGE_CLICK_TEXT", "")   # text of a button INSIDE the mini app to click (e.g. Start)
BUTTON_TEXT = cfg("BUTTON_TEXT", "")        # exact button label to press; empty = first button
PHONE_MODE  = cfg("PHONE_MODE", "http")     # "http" = fetch link in code, "phone" = try phone browser
# ---------------------------------------------------------------------------


def open_without_playwright(url):
    """Used when Playwright is not available (Pydroid)."""
    if PHONE_MODE == "phone":
        try:
            r = subprocess.run(
                ["am", "start", "-a", "android.intent.action.VIEW", "-d", url],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0 and "Error" not in (r.stdout + r.stderr):
                log.info("Opened in phone browser")
                return
        except Exception as e:
            log.info("am start failed: %s", e)
        try:
            if webbrowser.open(url):
                log.info("Opened with webbrowser")
                return
        except Exception as e:
            log.info("webbrowser failed: %s", e)
        log.info("Phone browser failed, using HTTP request instead")
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,*/*",
        })
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = resp.read()
            log.info("HTTP %s, %d bytes loaded", resp.status, len(body))
    except Exception as e:
        log.warning("HTTP load failed: %s", e)


async def webview_url(client, raw):
    """Open a Telegram Mini App button and return the real web URL."""
    bot = await client.get_input_entity(BOT)
    theme = types.DataJSON(data='{"bg_color":"#ffffff","text_color":"#000000",'
                                '"button_color":"#3390ec","button_text_color":"#ffffff"}')
    try:
        if type(raw).__name__ == "KeyboardButtonWebView":
            res = await client(functions.messages.RequestWebViewRequest(
                peer=bot, bot=bot, platform="android", url=raw.url, theme_params=theme))
        else:
            res = await client(functions.messages.RequestSimpleWebViewRequest(
                bot=bot, platform="android", url=raw.url, theme_params=theme))
        return res.url
    except Exception as e:
        log.warning("Mini app request failed: %r", e)
        return None


def buttons_of(msg):
    """Raw buttons of a message (no Telethon helper classes)."""
    markup = getattr(msg, "reply_markup", None)
    out = []
    for row in getattr(markup, "rows", None) or []:
        out.extend(getattr(row, "buttons", None) or [])
    return out


async def find_url(client, msg):
    """URL from a link button, a Mini App button, or the message text."""
    for raw in buttons_of(msg):
        kind = type(raw).__name__
        if kind == "KeyboardButtonUrl":
            return raw.url
        if kind in ("KeyboardButtonWebView", "KeyboardButtonSimpleWebView"):
            text = getattr(raw, "text", "") or ""
            if BUTTON_TEXT and BUTTON_TEXT.lower() not in text.lower():
                continue
            log.info("Opening Mini App button: %s", text)
            url = await webview_url(client, raw)
            if url:
                return url
    m = re.search(r"https?://\S+", msg.text or "")
    return m.group(0) if m else None


async def press_button(client, msg):
    """Press a normal (callback / plain) button. Returns a URL if the answer has one."""
    bot = await client.get_input_entity(BOT)
    cands = [r for r in buttons_of(msg)
             if type(r).__name__ in ("KeyboardButtonCallback", "KeyboardButton")]
    if BUTTON_TEXT:
        cands = [r for r in cands if BUTTON_TEXT.lower() in (r.text or "").lower()]
    if not cands:
        return None
    raw = cands[0]
    log.info("Pressing button: %s", raw.text)
    if type(raw).__name__ == "KeyboardButtonCallback":
        res = await client(functions.messages.GetBotCallbackAnswerRequest(
            peer=bot, msg_id=msg.id, data=raw.data))
        return getattr(res, "url", None)
    await client.send_message(BOT, raw.text)
    return None


async def get_link(client):
    """Send TEXT, open the Mini App button / press the button, return the URL."""
    async with client.conversation(BOT, timeout=45) as conv:
        async with client.action(BOT, "typing"):
            await asyncio.sleep(random.uniform(0.5, 1.5))
        await conv.send_message(TEXT)
        for _ in range(4):
            try:
                msg = await conv.get_response()
            except asyncio.TimeoutError:
                log.info("Bot sent no (more) messages")
                break
            await asyncio.sleep(random.uniform(0.8, 2.5))   # "reading" time

            url = await find_url(client, msg)
            if url:
                return url

            if buttons_of(msg):
                try:
                    url = await press_button(client, msg)
                    if url:
                        return url
                except Exception as e:
                    log.warning("Button press failed: %r", e)
                await asyncio.sleep(random.uniform(1, 2))
                fresh = await client.get_messages(BOT, ids=msg.id)   # may be edited
                url = await find_url(client, fresh) if fresh else None
                if url:
                    return url
    return None


async def main():
    session = StringSession(SESSION) if SESSION else "session"
    client = TelegramClient(session, API_ID, API_HASH)
    await client.start()          # first run asks phone + code
    log.info("Telegram connected")
    import telethon
    log.info("Script v6 | Telethon %s", getattr(telethon, "__version__", "?"))

    await client.send_message(BOT, "/start")
    await asyncio.sleep(random.uniform(3, 7))

    pw = browser = None
    if HAS_PW:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
    else:
        log.info("Playwright not found: using phone/default browser")

    async def open_url(url):
        stay = random.uniform(STAY_MIN, STAY_MAX)
        if browser:
            ctx = await browser.new_context(
                user_agent=("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"),
                viewport={"width": 390, "height": 844},
                is_mobile=True, has_touch=True,
            )
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="load", timeout=60000)
                log.info("Mini app loaded")
                if PAGE_CLICK_TEXT:
                    try:
                        await page.get_by_text(PAGE_CLICK_TEXT, exact=False).first.click(timeout=20000)
                        log.info("Clicked '%s' inside the page", PAGE_CLICK_TEXT)
                    except Exception as e:
                        log.info("Page button not found: %r", e)
                await page.wait_for_timeout(int(stay * 1000))
            finally:
                await ctx.close()
        else:
            await asyncio.to_thread(open_without_playwright, url)
            await asyncio.sleep(stay)

    day_start = time.time()
    today = rounds = floods = 0
    until_break = random.randint(25, 45)

    while True:
        # reset the daily counter after 24h; pause if limit reached
        if time.time() - day_start > 86400:
            day_start, today = time.time(), 0
        if today >= MAX_PER_DAY:
            wait = 86400 - (time.time() - day_start)
            log.info("Daily limit reached. Sleeping %.1f h", wait / 3600)
            await asyncio.sleep(max(wait, 60))
            continue

        try:
            url = await get_link(client)
            if url:
                log.info("Opening %s", url)
                await open_url(url)
                today += 1
                rounds += 1
                log.info("Done. Today: %d/%d", today, MAX_PER_DAY)
            else:
                log.info("No button/link received")
        except FloodWaitError as e:
            floods += 1
            log.warning("FloodWait %ss (%d/3)", e.seconds, floods)
            if floods >= 3:
                log.warning("Too many flood waits. Stopping to protect account.")
                break
            await asyncio.sleep(e.seconds + random.randint(60, 180))
            continue
        except Exception as e:
            log.warning("Error: %r", e, exc_info=True)

        # random pause between rounds
        # mostly short pauses, occasionally a slightly longer one (like a real person)
        pause = random.triangular(MIN_DELAY, MAX_DELAY, MIN_DELAY + (MAX_DELAY - MIN_DELAY) * 0.3)
        if random.random() < 0.08:
            pause += random.uniform(10, 30)
        await asyncio.sleep(pause)

        # every 25-45 rounds take a longer "human" break (3-5 min)
        until_break -= 1
        if until_break <= 0:
            br = random.uniform(180, 300)
            log.info("Taking a break: %.0f min", br / 60)
            await asyncio.sleep(br)
            until_break = random.randint(25, 45)

    if browser:
        await browser.close()
    if pw:
        await pw.stop()
    await client.disconnect()


asyncio.run(main())
