import io
import os
import re


import pandas as pd
import plotly.express as px
import streamlit as st
import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine, text




load_dotenv()
st.set_page_config(page_title="TikTok Live Manager Dashboard", page_icon="⚓", layout="wide")




def quote_identifier(name: str) -> str:
    if not name or not all(part.replace("_", "").isalnum() for part in name.split(".")):
        raise ValueError(f"Unsafe database identifier: {name!r}")
    return ".".join(f'"{part}"' for part in name.split("."))




def secret_value(name: str, default=""):
    value = os.getenv(name, default)
    try:
        return st.secrets.get(name, value)
    except Exception:
        return value




@st.cache_resource
def load_settings():
    with open("config.yaml", "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)




@st.cache_resource
def get_engine():
    url = secret_value("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is missing. Add it to Streamlit Secrets or your local .env file.")
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    elif url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgres://")
    return create_engine(url, pool_pre_ping=True)




@st.cache_resource
def ensure_schema():
    statements = [
        "CREATE TABLE IF NOT EXISTS creators (id TEXT PRIMARY KEY, display_name TEXT, tiktok_username TEXT, account_status TEXT, agency_name TEXT, manager_name TEXT, country TEXT, joined_at TEXT, last_active_at TEXT)",
        "CREATE TABLE IF NOT EXISTS manager_performance (manager TEXT PRIMARY KEY, active_creators INTEGER, live_streams INTEGER, valid_live_creators INTEGER, live_hours REAL, creators_under_15h_pct REAL, diamonds INTEGER, diamond_goal INTEGER, diamond_change_pct REAL, period_start TEXT, period_end TEXT)",
        "CREATE TABLE IF NOT EXISTS goal_creators (creator_id TEXT PRIMARY KEY, username TEXT, manager TEXT, manager_name TEXT, group_name TEXT, diamonds INTEGER, valid_live_days INTEGER, valid_live_hours REAL, estimated_bonus REAL, tier_status TEXT, rank_up_progress TEXT, activeness_level INTEGER, live_now INTEGER)",
        "CREATE TABLE IF NOT EXISTS goal_managers (manager TEXT PRIMARY KEY, manager_name TEXT, role TEXT, group_name TEXT, diamonds INTEGER, diamond_goal INTEGER, new_creators INTEGER, new_creator_goal INTEGER, managed_creators INTEGER)",
        "CREATE TABLE IF NOT EXISTS data_updates (updated_at TEXT, source_file TEXT, creator_rows INTEGER)",
        "CREATE TABLE IF NOT EXISTS collector_runs (started_at TEXT, finished_at TEXT, status TEXT, detail TEXT, creator_rows INTEGER)",
    ]
    with get_engine().begin() as connection:
        for statement in statements:
            connection.execute(text(statement))




@st.cache_data(ttl=300)
def load_creators():
    cfg = load_settings()["creators"]
    table = quote_identifier(cfg["table"])
    columns = cfg["columns"]
    selected = []
    for standard_name, database_name in columns.items():
        if database_name:
            selected.append(f'{quote_identifier(database_name)} AS "{standard_name}"')
    sql = text(f"SELECT {', '.join(selected)} FROM {table}")
    with get_engine().connect() as connection:
        return pd.read_sql(sql, connection)




@st.cache_data(ttl=300)
def load_manager_performance():
    with get_engine().connect() as connection:
        return pd.read_sql(text("SELECT * FROM manager_performance"), connection)




@st.cache_data(ttl=300)
def load_goal_managers():
    with get_engine().connect() as connection:
        return pd.read_sql(text("SELECT * FROM goal_managers"), connection)




@st.cache_data(ttl=300)
def load_goal_creators():
    with get_engine().connect() as connection:
        return pd.read_sql(text("SELECT * FROM goal_creators"), connection)



def numeric_series(frame, column):
    if column not in frame.columns:
        return pd.Series(0, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce").fillna(0)


def manager_series(frame):
    for column in ("manager_name", "manager"):
        if column in frame.columns:
            values = frame[column].fillna("").astype(str).str.strip()
            if values.ne("").any():
                return values
    return pd.Series("Unassigned", index=frame.index, dtype="object")


def main():

    st.markdown(
        """
        <style>
        :root { --gh-navy: #030817; --gh-deep: #071a3a; --gh-blue: #102d6b; --gh-gold: #f5c542; --gh-gold-deep: #c99416; --gh-violet: #8b5cf6; --gh-text: #eef4ff; }
        .stApp {
          background:
            radial-gradient(circle at 86% 2%, rgba(111, 69, 202, .27), transparent 24rem),
            radial-gradient(circle at 18% 0%, rgba(19, 80, 184, .24), transparent 28rem),
            linear-gradient(150deg, var(--gh-navy) 0%, #06142f 48%, #02050e 100%);
          color: var(--gh-text);
        }
        [data-testid="stHeader"] { background: rgba(3, 8, 23, .70); border-bottom: 1px solid rgba(245, 197, 66, .20); }
        [data-testid="stSidebar"] { background: linear-gradient(180deg, #030817 0%, #071a3a 55%, #030817 100%); border-right: 1px solid rgba(245, 197, 66, .38); }
        [data-testid="stSidebar"] * { color: var(--gh-text); }
        [data-testid="stSidebar"] [data-baseweb="select"] > div { background: rgba(9, 27, 64, .9); border-color: rgba(245, 197, 66, .5); }
        .gh-brand { color: var(--gh-gold); font-weight: 800; letter-spacing: .18em; font-size: .82rem; margin: .55rem 0 .15rem; text-shadow: 0 0 16px rgba(245, 197, 66, .45); }
        h1 { color: var(--gh-gold) !important; letter-spacing: -.02em; text-shadow: 0 2px 18px rgba(245, 197, 66, .28); }
        h2, h3 { color: #f4d577 !important; }
        [data-testid="stCaptionContainer"] p { color: #b9c6e4 !important; }
        [data-testid="stMetric"] { background: linear-gradient(145deg, rgba(18, 48, 109, .78), rgba(4, 12, 31, .92)); border: 1px solid rgba(245, 197, 66, .42); border-radius: 14px; box-shadow: inset 0 1px 0 rgba(255,255,255,.08), 0 14px 28px rgba(0,0,0,.20); padding: 1rem; }
        [data-testid="stMetricLabel"] { color: #cad7f5 !important; font-weight: 650; }
        [data-testid="stMetricValue"] { color: var(--gh-gold) !important; font-weight: 800; text-shadow: 0 0 14px rgba(245, 197, 66, .22); }
        [data-testid="stDataFrame"] { border: 1px solid rgba(245, 197, 66, .38); border-radius: 12px; overflow: hidden; box-shadow: 0 12px 32px rgba(0,0,0,.22); }
        [data-testid="stDataFrame"] [role="columnheader"] { background: #0c2860 !important; color: #f7d56d !important; }
        hr { border-color: rgba(245, 197, 66, .32) !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown('<div class="gh-brand">⚓ GRACE HARBOUR MEDIA &nbsp;•&nbsp; CREATOR NETWORK</div>', unsafe_allow_html=True)
    st.title("TikTok Live Manager Dashboard")

    st.caption("Current creator goals from the latest authorized Backstage capture.")

    try:
        ensure_schema()
        managers = load_goal_managers()
        creators = load_goal_creators()
    except Exception:
        st.error("The dashboard could not read its data store. Please try refreshing in a moment.")
        st.stop()

    if creators.empty:
        st.info("No creator-goal records have been imported yet.")
        st.stop()

    creators = creators.copy()
    creators["_manager"] = manager_series(creators)
    managers = managers.copy()
    if not managers.empty:
        managers["_manager"] = manager_series(managers)

    manager_names = sorted(name for name in creators["_manager"].unique() if name and name != "Unassigned")
    choice = st.sidebar.selectbox("View manager", ["All managers", *manager_names])
    visible = creators if choice == "All managers" else creators[creators["_manager"] == choice].copy()
    visible_diamonds = numeric_series(visible, "diamonds")
    tier_text = visible.get("tier_status", pd.Series("", index=visible.index)).fillna("").astype(str).str.lower()
    rank_text = visible.get("rank_up_progress", pd.Series("", index=visible.index)).fillna("").astype(str).str.lower()

    ranked_up = int((tier_text.str.contains("rank") | rank_text.str.contains("rank")).sum())
    maintained = int((tier_text.str.contains("maintain") | rank_text.str.contains("maintain")).sum())
    above_200k = int((visible_diamonds >= 200000).sum())

    selected_manager_rows = managers if choice == "All managers" else managers[managers["_manager"] == choice]
    new_creators = int(numeric_series(selected_manager_rows, "new_creators").sum()) if not selected_manager_rows.empty else 0

    first, second, third, fourth, fifth = st.columns(5)
    first.metric("Creators", f"{len(visible):,}")
    second.metric("Diamonds", f"{int(visible_diamonds.sum()):,}")
    third.metric("New creators", f"{new_creators:,}")
    fourth.metric("Maintaining tier", f"{maintained:,}")
    fifth.metric("Ranking up", f"{ranked_up:,}")
    st.caption(f"Creators above 200k diamonds: {above_200k:,}")

    st.subheader("Creator goals")
    display = pd.DataFrame({
        "Creator": visible.get("username", visible.get("creator_id", pd.Series("", index=visible.index))),
        "Manager": visible["_manager"],
        "Diamonds": visible_diamonds.astype("int64"),
        "Valid go LIVE days": numeric_series(visible, "valid_live_days").astype("int64"),
        "Valid LIVE duration": numeric_series(visible, "valid_live_hours"),
        "Bonus contribution": numeric_series(visible, "estimated_bonus"),
        "Tier": visible.get("tier_status", pd.Series("", index=visible.index)),
        "Rank status": visible.get("rank_up_progress", pd.Series("", index=visible.index)),
        "Activeness": visible.get("activeness_level", pd.Series("", index=visible.index)),
        "Live now": visible.get("live_now", pd.Series("", index=visible.index)),
    })
    if choice != "All managers":
        display = display.drop(columns=["Manager"])
    st.dataframe(display.sort_values("Diamonds", ascending=False), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
