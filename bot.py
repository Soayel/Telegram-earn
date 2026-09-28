import asyncio, os, re, logging
from telethon import TelegramClient
from telethon.sessions import StringSession
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("bot")

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION = os.environ["SESSION"]          # from gen_session.py
BOT = os.environ["BOT"]                  # e.g. @your_bot
TEXT = os.environ["TEXT"]                # text to send each round
DELAY = int(os.environ.get("DELAY", "5"))          # seconds between rounds
STAY = int(os.environ.get("STAY", "3"))             # seconds to stay on page


async def get_link(client):
    """Send TEXT, wait for reply with a button (or link) and return its URL."""
    async with client.conversation(BOT, timeout=30) as conv:
        await conv.send_message(TEXT)
        for _ in range(5):
            msg = await conv.get_response()
            for row in msg.buttons or []:
                for b in row:
                    if b.url:
                        return b.url
            m = re.search(r"https?://\S+", msg.text or "")
            if m:
                return m.group(0)
    return None


async def main():
    client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
    await client.start()
    log.info("Telegram connected")

    # Start the bot once
    await client.send_message(BOT, "/start")
    await asyncio.sleep(2)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        while True:
            page = None
            try:
                url = await get_link(client)
                if not url:
                    log.info("No button/link received, retrying")
                else:
                    log.info("Opening %s", url)
                    page = await browser.new_page()
                    await page.goto(url, wait_until="load", timeout=60000)
                    await page.wait_for_timeout(STAY * 1000)
                    log.info("Page loaded")
            except Exception as e:
                log.warning("Error: %s", e)
            finally:
                if page:
                    await page.close()
            await asyncio.sleep(DELAY)


asyncio.run(main())
