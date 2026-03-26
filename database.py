import sqlite3
import logging
from pathlib import Path

ROOT_DIR = Path(__file__).parent
DB_PATH = ROOT_DIR / "mentions.db"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def init_db():
    """Initializes the SQLite database and creates required tables."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Main processed mentions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS processed_mentions (
            tweet_url TEXT PRIMARY KEY,
            username TEXT,
            comment TEXT,
            action TEXT,
            category TEXT,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # FIX: Added failed_mentions table for retry queue
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS failed_mentions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tweet_url TEXT,
            username TEXT,
            comment TEXT,
            error TEXT,
            retry_count INTEGER DEFAULT 0,
            failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()
    logger.info("✅ Database initialized.")


def is_processed(tweet_url: str) -> bool:
    """Checks if a tweet URL already exists in the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM processed_mentions WHERE tweet_url = ?', (tweet_url,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def mark_as_processed(
    tweet_url: str,
    username: str = None,
    comment: str = None,
    action: str = None,
    category: str = None
):
    """Marks a tweet as processed. Silently ignores duplicates."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            '''
            INSERT INTO processed_mentions
            (tweet_url, username, comment, action, category)
            VALUES (?, ?, ?, ?, ?)
            ''',
            (tweet_url, username, comment, action, category)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # Already processed — safe to ignore
    finally:
        conn.close()


def mark_as_failed(tweet_url: str, username: str, comment: str, error: str):
    """
    FIX: Logs failed tweets to a retry queue instead of silently dropping them.
    Increments retry_count if already failed before.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            'SELECT id, retry_count FROM failed_mentions WHERE tweet_url = ?',
            (tweet_url,)
        )
        row = cursor.fetchone()

        if row:
            cursor.execute(
                'UPDATE failed_mentions SET retry_count = ?, error = ?, failed_at = CURRENT_TIMESTAMP WHERE id = ?',
                (row[1] + 1, error, row[0])
            )
        else:
            cursor.execute(
                'INSERT INTO failed_mentions (tweet_url, username, comment, error) VALUES (?, ?, ?, ?)',
                (tweet_url, username, comment, error)
            )

        conn.commit()
    finally:
        conn.close()


def get_failed_mentions(max_retries: int = 3) -> list:
    """Returns failed mentions that haven't exceeded max_retries."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        'SELECT tweet_url, username, comment FROM failed_mentions WHERE retry_count < ?',
        (max_retries,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [{"tweet_url": r[0], "username": r[1], "comment": r[2]} for r in rows]


def clear_failed_mention(tweet_url: str):
    """Removes a tweet from the failed queue after successful retry."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM failed_mentions WHERE tweet_url = ?', (tweet_url,))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
