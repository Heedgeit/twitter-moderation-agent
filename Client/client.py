import asyncio
import os
import json
import sqlite3
import logging
from pathlib import Path
from dotenv import load_dotenv

from database import is_processed, mark_as_processed, mark_as_failed, init_db, DB_PATH

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------ CONFIG ------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
load_dotenv(ENV_PATH)

# FIX: Centralised keyword list — no more duplication between parse_llm_output and main()
HARMFUL_KEYWORDS = ["fake", "scam", "fraud", "hate", "spam", "threat"]


# ------------------ TWITTER CLIENT ------------------
class TwitterMCPClient:

    def __init__(self):
        self.max_mentions = 20
        # FIX: Use ROOT_DIR-based path for auth.json — consistent regardless of working dir
        self.auth_file = ROOT_DIR / "auth.json"

        init_db()

        self.processed_cache: set = set()
        self._load_processed_cache()

    def _load_processed_cache(self):
        """Load processed tweet URLs into memory for fast dedup."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT tweet_url FROM processed_mentions")
        rows = cursor.fetchall()
        conn.close()

        self.processed_cache = {r[0] for r in rows}
        logger.info(f"⚡ Loaded {len(self.processed_cache)} processed tweets into cache")

    async def scroll_until_end(self, page, max_scrolls: int = 15):
        prev = 0
        for i in range(max_scrolls):
            tweets = page.locator("article")
            count = await tweets.count()
            logger.info(f"📜 Scroll {i+1}: {count} tweets visible")

            if count == prev:
                break

            prev = count
            await page.mouse.wheel(0, 6000)
            await asyncio.sleep(2)

    async def get_mentions(self) -> list:
        mentions = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"]
            )

            if not self.auth_file.exists():
                logger.error(f"❌ auth.json not found at {self.auth_file}")
                return []

            context = await browser.new_context(storage_state=str(self.auth_file))
            page = await context.new_page()

            try:
                logger.info("🌐 Fetching mentions...")

                for attempt in range(3):
                    try:
                        await page.goto(
                            "https://twitter.com/notifications/mentions",
                            wait_until="domcontentloaded",
                            timeout=60000
                        )
                        await page.wait_for_selector("article", timeout=15000)
                        break
                    except Exception as e:
                        logger.warning(f"⚠️ Retry {attempt+1}/3: {e}")
                        await asyncio.sleep(3)
                else:
                    logger.error("❌ Could not load mentions page after 3 attempts")
                    return []

                await asyncio.sleep(5)

                if await page.locator("input[autocomplete='username']").count() > 0:
                    logger.error("❌ Session expired — please regenerate auth.json")
                    return []

                await self.scroll_until_end(page)

                tweets = page.locator("article")
                count = await tweets.count()
                logger.info(f"🔎 Found {count} tweet articles")

                for i in range(min(count, self.max_mentions)):
                    tweet = tweets.nth(i)

                    try:
                        link = tweet.locator("a[href*='/status/']").first
                        if await link.count() == 0:
                            continue

                        tweet_url = await link.get_attribute("href")
                        if not tweet_url.startswith("http"):
                            tweet_url = "https://twitter.com" + tweet_url

                        if tweet_url in self.processed_cache or is_processed(tweet_url):
                            logger.info(f"⏭️ Already processed: {tweet_url}")
                            continue

                        text_el = tweet.locator("div[data-testid='tweetText']").first
                        comment = await text_el.inner_text() if await text_el.count() else ""

                        user_el = tweet.locator("div[data-testid='User-Name']").first
                        username_raw = await user_el.inner_text() if await user_el.count() else "user"

                        mentions.append({
                            "tweet_url": tweet_url,
                            "username": username_raw,
                            "comment": comment
                        })

                    except Exception as e:
                        logger.warning(f"⚠️ Skipped tweet at index {i}: {e}")
                        continue

            finally:
                await browser.close()

        return mentions


# ------------------ CLASSIFIER ------------------
def is_harmful(comment: str) -> bool:
    """
    FIX: Single source of truth for harmful keyword detection.
    Used by both fast-path and LLM fallback — no more duplicate logic.
    """
    lower = comment.lower()
    return any(kw in lower for kw in HARMFUL_KEYWORDS)


def parse_llm_output(text: str, comment: str) -> dict:
    """Parse LLM JSON output with a graceful fallback."""
    logger.debug(f"🤖 Raw LLM output:\n{text}\n")

    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        parsed = json.loads(text[start:end])

        return {
            "tool": parsed.get("tool", "ignore"),
            "category": parsed.get("category", "other"),
            "arguments": parsed.get("arguments", {})
        }

    except (json.JSONDecodeError, ValueError):
        # FIX: Fallback uses shared is_harmful() — no duplication
        if is_harmful(comment):
            return {"tool": "send_moderator_alert", "category": "spam", "arguments": {}}

        if "?" in comment:
            return {"tool": "reply_to_tweet", "category": "question", "arguments": {}}

        return {"tool": "ignore", "category": "other", "arguments": {}}


# ------------------ MAIN ------------------
async def main():
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    logger.info(f"🔑 Telegram configured: token={bool(telegram_token)}, chat={bool(telegram_chat_id)}")

    client = MultiServerMCPClient({
        "reply": {
            "command": "python",
            "args": ["-m", "servers.r_server"],
            "transport": "stdio",
            "env": {
                "TELEGRAM_BOT_TOKEN": telegram_token or "",
                "TELEGRAM_CHAT_ID": telegram_chat_id or "",
                "PYTHONPATH": str(ROOT_DIR),
                "PYTHONUNBUFFERED": "1"
            }
        },
        "alert": {
            "command": "python",
            "args": ["-m", "servers.alert"],
            "transport": "stdio",
            "env": {
                "TELEGRAM_BOT_TOKEN": telegram_token or "",
                "TELEGRAM_CHAT_ID": telegram_chat_id or "",
                "PYTHONPATH": str(ROOT_DIR),
                "PYTHONUNBUFFERED": "1"
            }
        }
    })

    tools = await client.get_tools()
    logger.info(f"✅ Tools loaded: {[t.name for t in tools]}")

    # Classification model — fast, low temperature for consistent JSON output
    model = ChatGroq(model="openai/gpt-oss-120b", temperature=0.1)
    # Reasoning model — slightly higher temperature for natural reply generation
    reasoning_model = ChatGroq(model="openai/gpt-oss-120b", temperature=0.4)

    SYSTEM_PROMPT = """You are a Twitter moderation assistant.

Return ONLY valid JSON — no markdown, no explanation.

Reply to a question:
{"tool":"reply_to_tweet","category":"question","arguments":{"reply_text":"<your reply here>"}}

Alert moderators for harmful content:
{"tool":"send_moderator_alert","category":"spam","arguments":{}}

Ignore benign content:
{"tool":"ignore","category":"compliment","arguments":{}}

Valid categories: question, complaint, spam, compliment, feedback, abuse, other
"""

    bot = TwitterMCPClient()
    mentions = await bot.get_mentions()

    logger.info(f"📊 New mentions to process: {len(mentions)}")

    for m in mentions:
        tweet_url = m["tweet_url"]
        username_raw = m["username"]
        comment = m["comment"]

        logger.info(f"\n➡️ Processing: {tweet_url}")

        try:
            # FIX: Use shared is_harmful() — single source of truth
            if is_harmful(comment):
                decision = {"tool": "send_moderator_alert", "category": "spam", "arguments": {}}
                logger.info("🚨 Harmful content detected — fast-path to alert")
            else:
                response = model.invoke([
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=f"{username_raw}: {comment}")
                ])
                decision = parse_llm_output(response.content, comment)

            tool_name = decision["tool"]
            category = decision.get("category", "other")
            args = decision["arguments"]

            logger.info(f"🧠 Decision: tool={tool_name}, category={category}")

            if tool_name == "ignore":
                mark_as_processed(tweet_url, username_raw, comment, "ignore", category)
                bot.processed_cache.add(tweet_url)
                logger.info("⏭️ Ignored")
                continue

            for tool in tools:
                if tool.name == tool_name:

                    if tool_name == "reply_to_tweet":
                        # Extract @handle from username_raw
                        handle = next(
                            (p.replace("@", "") for p in username_raw.split() if p.startswith("@")),
                            "unknown"
                        )

                        reply_text = args.get("reply_text", "").strip()

                        if len(reply_text) < 10:
                            logger.info("🧠 Generating smart reply via reasoning model...")

                            prompt = (
                                f'Write a helpful, natural reply to this tweet:\n\n"{comment}"\n\n'
                                "Be clear and informative. Do not mention usernames."
                            )

                            try:
                                res = reasoning_model.invoke([HumanMessage(content=prompt)])
                                reply_text = res.content.strip()

                                if len(reply_text) < 10:
                                    raise ValueError("Reply too short")

                            except Exception as e:
                                logger.warning(f"⚠️ Reasoning model failed: {e}")
                                reply_text = "Thanks for reaching out. We'll assist you shortly."

                        tool_input = {
                            "tweet_url": tweet_url,
                            "reply_text": reply_text,
                            "username": handle,
                            "auth_file": str(ROOT_DIR / "auth.json")
                        }
                        action = "reply"

                    elif tool_name == "send_moderator_alert":
                        tool_input = {
                            "username": username_raw,
                            "comment": comment,
                            "tweet_url": tweet_url
                        }
                        logger.info(f"🚨 Alert payload: {tool_input}")
                        action = "alert"

                    else:
                        tool_input = args
                        action = "unknown"

                    result = await tool.ainvoke(tool_input)
                    logger.info(f"📬 Tool result: {result}")

                    mark_as_processed(tweet_url, username_raw, comment, action, category)
                    bot.processed_cache.add(tweet_url)
                    break

        except Exception as e:
            logger.error(f"❌ Error processing {tweet_url}: {e}")
            # FIX: Log failures to DB retry queue instead of silently dropping
            mark_as_failed(tweet_url, username_raw, comment, str(e))

    logger.info("\n✨ Done.")


if __name__ == "__main__":
    asyncio.run(main())
