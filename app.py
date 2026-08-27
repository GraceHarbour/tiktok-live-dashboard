import io
import json
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



def load_business_essentials():
    with get_engine().connect() as connection:
        return pd.read_sql(text("SELECT section, snapshot_month, row_key, row_index, payload, captured_at FROM business_essentials_rows ORDER BY captured_at DESC, row_index ASC"), connection)


def load_access_people():
    with get_engine().connect() as connection:
        return pd.read_sql(text("SELECT email, role, added_at FROM dashboard_access_people ORDER BY role, email"), connection)


def load_monthly_metrics():
    with get_engine().connect() as connection:
        return pd.read_sql(text("SELECT metric_name, metric_value, updated_at FROM dashboard_monthly_metrics ORDER BY metric_name"), connection)


def business_records(frame):
    rows = []
    for _, source in frame.iterrows():
        payload = source.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                continue
        if not isinstance(payload, dict):
            continue
        headers = payload.get("headers") or []
        values = payload.get("row") or []
        if not headers or not values:
            continue
        row = {str(header): values[index] if index < len(values) else "" for index, header in enumerate(headers)}
        if str(row.get("Record type", "")).casefold() == "overview":
            continue
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    st.markdown(
        """
        <style>
        :root { --gh-navy: #030817; --gh-deep: #071a3a; --gh-blue: #102d6b; --gh-gold: #f5c542; --gh-violet: #8b5cf6; --gh-text: #eef4ff; }
        .stApp { background: radial-gradient(circle at 86% 2%, rgba(111,69,202,.27), transparent 24rem), radial-gradient(circle at 18% 0%, rgba(19,80,184,.24), transparent 28rem), linear-gradient(150deg, var(--gh-navy), #06142f 48%, #02050e); color: var(--gh-text); }
        [data-testid="stHeader"] { background: rgba(3,8,23,.70); border-bottom: 1px solid rgba(245,197,66,.20); }
        [data-testid="stSidebar"] { background: linear-gradient(180deg,#030817,#071a3a 55%,#030817); border-right: 1px solid rgba(245,197,66,.38); }
        [data-testid="stSidebar"] * { color: var(--gh-text); }
        .gh-brand { color: var(--gh-gold); font-weight: 800; letter-spacing: .18em; font-size: .82rem; margin: .55rem 0 .15rem; text-shadow: 0 0 16px rgba(245,197,66,.45); }
        h1 { color: var(--gh-gold) !important; text-shadow: 0 2px 18px rgba(245,197,66,.28); }
        h2,h3 { color: #f4d577 !important; }
        [data-testid="stMetric"] { background: linear-gradient(145deg,rgba(18,48,109,.78),rgba(4,12,31,.92)); border: 1px solid rgba(245,197,66,.42); border-radius: 14px; padding: 1rem; }
        [data-testid="stMetricValue"] { color: var(--gh-gold) !important; }
        [data-testid="stDataFrame"] { border: 1px solid rgba(245,197,66,.38); border-radius: 12px; overflow: hidden; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown('<div class="gh-brand">⚓ GRACE HARBOUR MEDIA &nbsp;•&nbsp; CREATOR NETWORK</div>', unsafe_allow_html=True)
    st.title("TikTok Live Manager Dashboard")

    try:
        ensure_schema()
        managers = load_goal_managers()
        creators = load_goal_creators()
        business_source = load_business_essentials()
        access_people = load_access_people()
        monthly_metrics = load_monthly_metrics()
    except Exception:
        st.error("The dashboard could not read its data store. Please try refreshing in a moment.")
        st.stop()

    creators = creators.copy()
    creators["_manager"] = manager_series(creators) if not creators.empty else pd.Series(dtype="object")
    managers = managers.copy()
    if not managers.empty:
        managers["_manager"] = manager_series(managers)
    business = business_records(business_source)
    manager_values = set(creators.get("_manager", pd.Series(dtype="object")).dropna().astype(str))
    if not business.empty and "Manager" in business.columns:
        manager_values.update(business["Manager"].dropna().astype(str))
    manager_names = sorted(name for name in manager_values if name and name != "Unassigned")
    choice = st.sidebar.selectbox("View manager", ["All managers", *manager_names])

    goals_tab, business_tab, access_tab = st.tabs(["Goal Management", "Business Essentials", "Access & Data"])

    with goals_tab:
        st.caption("Current Creator-tab Goal Management records from the latest authorized Backstage capture.")
        if creators.empty:
            st.info("No creator-goal records have been imported yet.")
        else:
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
            display = pd.DataFrame({
                "Creator": visible.get("username", visible.get("creator_id", pd.Series("", index=visible.index))),
                "Manager": visible["_manager"], "Diamonds": visible_diamonds.astype("int64"),
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
            st.subheader("Creator goals")
            st.dataframe(display.sort_values("Diamonds", ascending=False), use_container_width=True, hide_index=True)

    with business_tab:
        st.caption("Business Essentials records from the latest complete Backstage capture.")
        if business.empty:
            st.info("No Business Essentials records have been imported yet.")
        else:
            if choice != "All managers" and "Manager" in business.columns:
                business = business[business["Manager"].fillna("").astype(str) == choice].copy()
            new_count = int(business.get("New this month", pd.Series(dtype="object")).astype(str).str.casefold().eq("true").sum())
            graduates = int(business.get("Mature creator", pd.Series(dtype="object")).astype(str).str.casefold().eq("true").sum())
            quitting = int(business.get("Quit on", pd.Series(dtype="object")).astype(str).str.casefold().eq("true").sum())
            one, two, three, four = st.columns(4)
            one.metric("Creators", f"{len(business):,}")
            two.metric("New this month", f"{new_count:,}")
            three.metric("Premium graduates", f"{graduates:,}")
            four.metric("Quit percentage", f"{(quitting / len(business) * 100) if len(business) else 0:.1f}%")
            st.dataframe(business, use_container_width=True, hide_index=True)

    with access_tab:
        st.caption("Current authorized-dashboard records and last saved metric values.")
        left, right = st.columns(2)
        with left:
            st.subheader("Access list")
            st.dataframe(access_people, use_container_width=True, hide_index=True)
        with right:
            st.subheader("Saved monthly metrics")
            st.dataframe(monthly_metrics, use_container_width=True, hide_index=True)
        if not business_source.empty:
            latest_business = pd.to_datetime(business_source["captured_at"], errors="coerce").max()
            st.caption(f"Latest Business Essentials capture: {latest_business}")


if __name__ == "__main__":
    main()
