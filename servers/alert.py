import os
import logging
import requests
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("Moderator Alert Server")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


@mcp.tool()
def send_moderator_alert(username: str, comment: str, tweet_url: str) -> dict:
    """
    Sends a Telegram alert to moderators when suspicious comments appear.

    Args:
        username: Twitter username of the commenter
        comment: The flagged comment text
        tweet_url: Direct URL to the tweet
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("❌ Telegram credentials not configured")
        return {
            "status": "error",
            "message": "Telegram credentials not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env"
        }

    # FIX: Escape HTML special chars to prevent Telegram parse errors
    safe_username = username.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_comment = comment.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    message = (
        "🚨 <b>Twitter Moderation Alert</b>\n\n"
        f"👤 <b>User:</b> {safe_username}\n\n"
        f"💬 <b>Comment:</b>\n{safe_comment}\n\n"
        f"🔗 <b>Link:</b> {tweet_url}"
    )

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }

        response = requests.post(url, json=payload, timeout=10)

        if response.status_code == 200:
            logger.info(f"✅ Alert sent for @{username}")
            return {
                "status": "success",
                "telegram_response": response.json()
            }
        else:
            logger.error(f"❌ Telegram API error: {response.text}")
            return {
                "status": "error",
                "message": f"Telegram API error: {response.text}"
            }

    except requests.exceptions.Timeout:
        logger.error("❌ Telegram request timed out")
        return {"status": "error", "message": "Request to Telegram timed out"}

    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        return {"status": "error", "message": f"Error sending alert: {str(e)}"}


if __name__ == "__main__":
    mcp.run(transport="stdio")
