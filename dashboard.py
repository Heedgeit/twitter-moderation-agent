import streamlit as st
import sqlite3
import pandas as pd
from pathlib import Path
 
DB_PATH = Path(__file__).parent / "mentions.db"
 
st.set_page_config(page_title="Twitter Bot Monitor", layout="wide")
st.title("🤖 Twitter Moderation Dashboard")
 
 
# ------------------ DB FUNCTIONS ------------------
def get_data() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
 
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT * FROM processed_mentions ORDER BY processed_at DESC",
        conn
    )
    conn.close()
    return df
 
 
def get_failed_data() -> pd.DataFrame:
    """FIX: Also surface failed mentions in the dashboard for visibility."""
    if not DB_PATH.exists():
        return pd.DataFrame()
 
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            "SELECT * FROM failed_mentions ORDER BY failed_at DESC",
            conn
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df
 
 
def clear_database():
    """Delete all records from processed_mentions and failed_mentions."""
    if not DB_PATH.exists():
        return
 
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM processed_mentions")
    cursor.execute("DELETE FROM failed_mentions")
    conn.commit()
    conn.close()
 
 
# ------------------ LOAD DATA ------------------
data = get_data()
failed_data = get_failed_data()
 
 
# ------------------ KPI ROW ------------------
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Processed", len(data))
col2.metric("Failed (Pending Retry)", len(failed_data))
 
if not data.empty:
    replies = len(data[data["action"] == "reply"])
    alerts = len(data[data["action"] == "alert"])
    col3.metric("Replies Sent", replies)
    col4.metric("Alerts Triggered", alerts)
 
 
# ------------------ CATEGORY BREAKDOWN ------------------
if not data.empty:
    st.markdown("---")
    st.subheader("📊 Category Breakdown")
 
    cat_counts = data["category"].value_counts().reset_index()
    cat_counts.columns = ["category", "count"]
    st.bar_chart(cat_counts.set_index("category"))
 
 
# ------------------ RECENT ACTIVITY ------------------
st.markdown("---")
st.subheader("🕒 Recent Activity")
 
if data.empty:
    st.info("No mentions processed yet.")
else:
    st.dataframe(data, use_container_width=True)
 
 
# ------------------ FAILED MENTIONS ------------------
if not failed_data.empty:
    st.markdown("---")
    st.subheader("⚠️ Failed Mentions (Retry Queue)")
    st.dataframe(failed_data, use_container_width=True)
 
 
# ------------------ ACTION BUTTONS ------------------
st.markdown("---")
colA, colB = st.columns(2)
 
with colA:
    if st.button("🔄 Refresh Data"):
        st.rerun()
 
with colB:
    if st.button("🧹 Reset Database"):
        clear_database()
        st.success("Database cleared successfully!")
        st.rerun()
