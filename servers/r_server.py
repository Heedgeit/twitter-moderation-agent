import asyncio
import json
import os
import sys
import logging
from mcp.server.fastmcp import FastMCP
from playwright.async_api import async_playwright, TimeoutError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("twitter_reply_server")

# FIX: headless mode is now configurable via env var — defaults to True for server/CI environments
HEADLESS = os.getenv("TWITTER_HEADLESS", "true").lower() == "true"

# FIX: Reply rate limit (seconds between replies) to avoid account flagging
REPLY_DELAY = int(os.getenv("TWITTER_REPLY_DELAY", "5"))


@mcp.tool()
async def reply_to_tweet(
    tweet_url: str,
    reply_text: str,
    username: str,
    auth_file: str = "auth.json"
) -> str:
    """
    Reply to a tweet as a direct child reply, mentioning the user.

    Args:
        tweet_url: URL of the tweet to reply to
        reply_text: Text content of the reply
        username: Twitter handle of commenter (without @)
        auth_file: Path to saved Twitter login session (auth.json)
    """
    full_reply = f"@{username} {reply_text}"

    try:
        async with async_playwright() as p:

            browser = await p.chromium.launch(
                # FIX: headless is now env-configurable
                headless=HEADLESS,
                slow_mo=50,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--disable-gpu"
                ]
            )

            # FIX: Use absolute path resolution for auth_file
            auth_path = os.path.abspath(auth_file)
            if not os.path.exists(auth_path):
                return json.dumps({
                    "status": "error",
                    "message": f"auth.json not found at: {auth_path}"
                })

            context = await browser.new_context(storage_state=auth_path)
            page = await context.new_page()

            logger.info(f"[Twitter] Opening tweet: {tweet_url}")
            await page.goto(tweet_url, timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(3000)

            if "login" in page.url:
                await browser.close()
                return json.dumps({
                    "status": "error",
                    "message": "Session expired. Please regenerate auth.json"
                })

            # Handle cookie popup
            try:
                cookie_button = page.locator("button:has-text('Accept')")
                if await cookie_button.count() > 0:
                    await cookie_button.first.click()
                    logger.info("[Twitter] Cookie popup accepted")
            except Exception:
                pass

            await page.mouse.wheel(0, 800)

            await page.wait_for_function(
                """() => {
                    return document.querySelector('button[data-testid="reply"]') ||
                           document.querySelector('div[data-testid="tweetTextarea_0"]');
                }""",
                timeout=45000
            )

            reply_button = page.locator("button[data-testid='reply']")
            if await reply_button.count() > 0:
                await reply_button.first.click()
                logger.info("[Twitter] Reply button clicked")

            await page.wait_for_function(
                """() => {
                    const editor = document.querySelector('[data-testid="tweetTextarea_0"]');
                    return editor && editor.getAttribute("contenteditable") === "true";
                }"""
            )

            textbox = page.locator("div[data-testid='tweetTextarea_0']").first
            await textbox.wait_for(state="visible", timeout=60000)
            await textbox.click(force=True)
            await page.wait_for_timeout(1500)

            # Clear anything prefilled
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")

            await page.keyboard.type(full_reply, delay=60)
            logger.info("[Twitter] Reply text typed")

            await asyncio.sleep(2)

            await page.wait_for_function(
                """() => {
                    const btn = document.querySelector(
                        'button[data-testid="tweetButtonInline"], button[data-testid="tweetButton"]'
                    );
                    return btn && !btn.disabled;
                }""",
                timeout=45000
            )

            send_button = page.locator(
                "button[data-testid='tweetButtonInline'], button[data-testid='tweetButton']"
            ).first
            await send_button.click()

            logger.info("[Twitter] Reply sent successfully")

            # FIX: Rate limiting — pause between replies to avoid account flagging
            await asyncio.sleep(REPLY_DELAY)

            await browser.close()

            return json.dumps({
                "status": "success",
                "tweet_url": tweet_url,
                "reply": full_reply
            })

    except TimeoutError:
        logger.error("[Twitter] Timeout while replying")
        return json.dumps({
            "status": "error",
            "message": "Timeout while interacting with Twitter UI"
        })

    except Exception as e:
        logger.error(f"[Twitter] Unexpected error: {e}")
        return json.dumps({
            "status": "error",
            "message": str(e)
        })


if __name__ == "__main__":
    mcp.run()
