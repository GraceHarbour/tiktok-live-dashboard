import base64

import io
import json
import os
import re
import requests
















import pandas as pd
import plotly.express as px
import streamlit as st
import yaml
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
































load_dotenv()
st.set_page_config(page_title="TikTok Live Manager Dashboard", page_icon="⚓", layout="wide")


@st.cache_data(show_spinner=False)
def banner_data_uri() -> str:
    banner = Path(__file__).resolve().parent / "assets" / "grace-harbour-approved-banner.png"
    return "data:image/png;base64," + base64.b64encode(banner.read_bytes()).decode("ascii")
































def quote_identifier(name: str) -> str:
    if not name or not all(part.replace("_", "").isalnum() for part in name.split(".")):
        raise ValueError(f"Unsafe database identifier: {name!r}")
    return ".".join(f'"{part}"' for part in name.split("."))
































def secret_value(name: str, default=""):
    value = os.getenv(name, default)
    if value:
        return value
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default






























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
    if ".pooler.supabase.com:5432/" in url:
        url = url.replace(".pooler.supabase.com:5432/", ".pooler.supabase.com:6543/")
    return create_engine(
        url,
        poolclass=NullPool,
        connect_args={"connect_timeout": 10, "sslmode": "require"},
    )
































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



def monthly_metric_value(metrics, name, default=0):
    if metrics.empty or "metric_name" not in metrics.columns or "metric_value" not in metrics.columns:
        return default
    matching = metrics[metrics["metric_name"].fillna("").astype(str).str.casefold() == str(name).casefold()]
    if matching.empty:
        return default
    return int(pd.to_numeric(matching["metric_value"], errors="coerce").fillna(default).iloc[-1])


def monthly_metric_float(metrics, name, default=0.0):
    if metrics.empty or "metric_name" not in metrics.columns or "metric_value" not in metrics.columns:
        return float(default)
    matching = metrics[metrics["metric_name"].fillna("").astype(str).str.casefold() == str(name).casefold()]
    if matching.empty:
        return float(default)
    return float(pd.to_numeric(matching["metric_value"], errors="coerce").fillna(default).iloc[-1])
















def manager_series(frame):
    for column in ("manager_name", "manager"):
        if column in frame.columns:
            values = frame[column].fillna("").astype(str).str.strip()
            if values.ne("").any():
                return values
    return pd.Series("Unassigned", index=frame.index, dtype="object")
























@st.cache_data(ttl=30)
def load_business_essentials():
    with get_engine().connect() as connection:
        return pd.read_sql(text("SELECT section, snapshot_month, row_key, row_index, payload, captured_at FROM business_essentials_rows WHERE snapshot_month = (SELECT MAX(snapshot_month) FROM business_essentials_rows) ORDER BY captured_at DESC, row_index ASC"), connection)
















@st.cache_data(ttl=300)
def load_access_people():
    with get_engine().connect() as connection:
        return pd.read_sql(text("SELECT email, role, active, added_at, updated_at FROM dashboard_access_people ORDER BY role, email"), connection)


def google_signed_in_email() -> str:
    try:
        signed_in = str(st.context.headers.get("X-Goog-Authenticated-User-Email", "")).strip()
    except Exception:
        return ""
    if ":" in signed_in:
        signed_in = signed_in.split(":", 1)[1]
    return signed_in.casefold()


def _google_access_token() -> str:
    response = requests.get(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"}, timeout=2,
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def google_iap_access_members() -> set[str]:
    """Read the actual project-level IAP access binding used by this dashboard."""
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "project-9ae1c2b9-2eb2-4f7f-8e8")
    token = _google_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    response = requests.post(f"https://cloudresourcemanager.googleapis.com/v1/projects/{project}:getIamPolicy", headers=headers, json={}, timeout=10)
    response.raise_for_status()
    return {
        member.split(":", 1)[1].casefold()
        for binding in response.json().get("bindings", [])
        if binding.get("role") == "roles/iap.httpsResourceAccessor"
        for member in binding.get("members", [])
        if member.startswith("user:")
    }


def set_google_iap_access(email: str, enabled: bool) -> None:
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "project-9ae1c2b9-2eb2-4f7f-8e8")
    normalized = email.strip().casefold()
    token = _google_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    policy_url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{project}:getIamPolicy"
    policy_response = requests.post(policy_url, headers=headers, json={}, timeout=10)
    policy_response.raise_for_status()
    policy = policy_response.json()
    binding = next((item for item in policy.setdefault("bindings", []) if item.get("role") == "roles/iap.httpsResourceAccessor"), None)
    if binding is None:
        binding = {"role": "roles/iap.httpsResourceAccessor", "members": []}
        policy["bindings"].append(binding)
    members = {str(member) for member in binding.get("members", [])}
    member = f"user:{normalized}"
    if enabled:
        members.add(member)
    else:
        members.discard(member)
    binding["members"] = sorted(members)
    update = requests.post(f"https://cloudresourcemanager.googleapis.com/v1/projects/{project}:setIamPolicy", headers=headers, json={"policy": policy}, timeout=10)
    update.raise_for_status()


def save_access_person(email: str, role: str) -> None:
    normalized_email = email.strip().casefold()
    if not normalized_email or "@" not in normalized_email:
        raise ValueError("Enter a valid Google email address.")
    if role not in {"member", "admin", "owner"}:
        raise ValueError("Choose a valid role.")
    with get_engine().begin() as connection:
        connection.execute(
            text("""INSERT INTO dashboard_access_people (email, role, active, added_at, updated_at)
                  VALUES (:email, :role, TRUE, NOW(), NOW())
                  ON CONFLICT (email) DO UPDATE
                  SET role = EXCLUDED.role, active = TRUE, updated_at = NOW()"""),
            {"email": normalized_email, "role": role},
        )


def deactivate_access_person(email: str) -> None:
    with get_engine().begin() as connection:
        connection.execute(
            text("UPDATE dashboard_access_people SET active = FALSE, updated_at = NOW() WHERE email = :email"),
            {"email": email.strip().casefold()},
        )
















@st.cache_data(ttl=300)
def load_monthly_metrics():
    with get_engine().connect() as connection:
        return pd.read_sql(text("SELECT metric_name, metric_value, updated_at FROM dashboard_monthly_metrics ORDER BY metric_name"), connection)


@st.cache_data(ttl=90)
def load_scouting_records():
    try:
        with get_engine().connect() as connection:
            return pd.read_sql(text("SELECT source, username, followers, likes, applied_to_join, scouting_status, live_streams, diamonds, live_hours, avg_live_viewers, invitation_type, assigned_manager, source_label, lead_expiry, captured_at FROM scouting_records ORDER BY source, ctid"), connection)
    except Exception:
        return pd.DataFrame(columns=["source", "username", "followers", "likes", "applied_to_join", "scouting_status", "live_streams", "diamonds", "live_hours", "avg_live_viewers", "invitation_type", "assigned_manager", "source_label", "lead_expiry", "captured_at"])

















def business_overview_measures(frame):
    overview = {}
    for _, source in frame.iterrows():
        payload = source.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                continue
        if not isinstance(payload, dict):
            continue
        candidate = payload.get("overview")
        if isinstance(candidate, dict):
            overview = candidate
            break
    if not overview:
        return pd.DataFrame(columns=["Category", "Measure", "Current", "Target"])

    label_keys = (
        "TaskName", "MetricName", "DisplayName", "Name", "Title",
        "Label", "DimensionName", "ConditionName", "Description",
    )
    current_keys = (
        "CurrentValue", "Current", "MetricValue", "Value", "Actual",
        "CompletedValue", "ProgressValue", "Count",
    )
    target_keys = (
        "TargetValue", "Target", "Goal", "TaskTarget",
        "TotalValue", "RequiredValue",
    )

    def pick(item, candidates):
        if not isinstance(item, dict):
            return None
        lowered = {str(key).casefold(): value for key, value in item.items()}
        for key in candidates:
            value = lowered.get(key.casefold())
            if value not in (None, "", [], {}):
                return value
        return None

    def text_value(value):
        if value is None or isinstance(value, (dict, list, tuple, set)):
            return ""
        return str(value).strip()

    rows = []
    seen = set()

    def walk(value, category, inherited_label="", depth=0):
        if depth > 7:
            return
        if isinstance(value, list):
            for item in value:
                walk(item, category, inherited_label, depth + 1)
            return
        if not isinstance(value, dict):
            return

        label = text_value(pick(value, label_keys)) or inherited_label
        current = pick(value, current_keys)
        target = pick(value, target_keys)
        current_text = text_value(current)
        target_text = text_value(target)
        if label and current_text:
            row = (category, label, current_text, target_text)
            if row not in seen:
                seen.add(row)
                rows.append({
                    "Category": category,
                    "Measure": label,
                    "Current": current_text,
                    "Target": target_text,
                })

        for key, child in value.items():
            if isinstance(child, (dict, list)):
                walk(child, category, label or str(key).replace("_", " ").title(), depth + 1)

    overview_groups = (
        ("Overview metrics", overview.get("Dimensions")),
        ("Business targets", overview.get("TaskTagList")),
        ("Progress toward targets", overview.get("TaskProgress")),
        ("Creator graduation", overview.get("GraduationLine")),
        ("Benefits and rewards", overview.get("Benefits")),
    )
    for category, value in overview_groups:
        walk(value, category)

    return pd.DataFrame(rows, columns=["Category", "Measure", "Current", "Target"])

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
        row["Section"] = str(source.get("section") or "Business Essentials")
        if str(row.get("Record type", "")).casefold() == "overview":
            continue
        rows.append(row)
    return pd.DataFrame(rows)




















@st.cache_data(ttl=60)
def load_shared_prior_month():
    try:
        with get_engine().connect() as connection:
            result = connection.execute(text(
                "SELECT file_name, sheet_name, columns_json, rows_json, uploaded_at "
                "FROM dashboard_prior_month_uploads WHERE id = 1"
            )).mappings().first()
        if not result:
            return None
        return {
            "file_name": result["file_name"],
            "sheet_name": result["sheet_name"],
            "columns": json.loads(result["columns_json"] or "[]"),
            "rows": json.loads(result["rows_json"] or "[]"),
            "uploaded_at": result["uploaded_at"],
        }
    except Exception:
        return None








def save_shared_prior_month(file_name, sheet_name, columns, frame):
    rows = frame.where(pd.notna(frame), None).to_dict(orient="records")
    with get_engine().begin() as connection:
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS dashboard_prior_month_uploads ("
            "id SMALLINT PRIMARY KEY CHECK (id = 1), "
            "file_name TEXT NOT NULL, sheet_name TEXT, columns_json TEXT NOT NULL, "
            "rows_json TEXT NOT NULL, uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
        ))
        connection.execute(text(
            "INSERT INTO dashboard_prior_month_uploads "
            "(id, file_name, sheet_name, columns_json, rows_json, uploaded_at) "
            "VALUES (1, :file_name, :sheet_name, :columns_json, :rows_json, NOW()) "
            "ON CONFLICT (id) DO UPDATE SET file_name = EXCLUDED.file_name, "
            "sheet_name = EXCLUDED.sheet_name, columns_json = EXCLUDED.columns_json, "
            "rows_json = EXCLUDED.rows_json, uploaded_at = EXCLUDED.uploaded_at"
        ), {
            "file_name": file_name,
            "sheet_name": sheet_name,
            "columns_json": json.dumps(columns, default=str),
            "rows_json": json.dumps(rows, default=str),
        })








def render_read_table(frame: pd.DataFrame, *, height: int | None = None) -> None:
    """Render dashboard data as a high-contrast, readable table instead of Streamlit's white grid."""
    if frame is None or frame.empty:
        st.info("No records match this view.")
        return
    visible = frame.copy().fillna("")
    max_height = f"max-height: {height}px;" if height else "max-height: 760px;"
    html_table = visible.to_html(index=False, escape=True, classes="gh-data-table")
    st.markdown(f'<div class="gh-data-panel" tabindex="0" aria-label="Scrollable creator data table" style="{max_height}">{html_table}</div>', unsafe_allow_html=True)


def main():
    st.markdown(
        """
        <style>
        :root { --gh-navy: #030817; --gh-deep: #071a3a; --gh-blue: #102d6b; --gh-gold: #f5c542; --gh-violet: #8b5cf6; --gh-text: #eef4ff; }
        .stApp { position: relative; overflow-x: hidden; background: radial-gradient(circle at 86% 2%, rgba(111,69,202,.27), transparent 24rem), radial-gradient(circle at 18% 0%, rgba(19,80,184,.24), transparent 28rem), linear-gradient(150deg, var(--gh-navy), #06142f 48%, #02050e); color: var(--gh-text); }
        .stApp::before { content: "⚓"; position: fixed;
# Deployment marker: apply reset database credential.
-index: 0; right: -3rem; top: 4rem; color: rgba(245,197,66,.010); font-size: 28rem; line-height: 1; transform: rotate(-11deg); pointer-events: none; }
        .stApp > * { position: relative; z-index: 1; }
        [data-testid="stHeader"] { background: rgba(3,8,23,.70); border-bottom: 1px solid rgba(245,197,66,.20); }
        [data-testid="stSidebar"] { background: linear-gradient(180deg,#030817,#071a3a 55%,#030817); border-right: 1px solid rgba(245,197,66,.38); }
        [data-testid="stSidebar"] * { color: var(--gh-text); }
        .gh-brand { color: var(--gh-gold); font-weight: 800; letter-spacing: .18em; font-size: .82rem; margin: .55rem 0 .15rem; text-shadow: 0 0 16px rgba(245,197,66,.45); }
        h1 { color: var(--gh-gold) !important; text-shadow: 0 2px 18px rgba(245,197,66,.28); }
        h2,h3 { color: #f4d577 !important; }
        [data-testid="stMetric"] { background: linear-gradient(145deg,rgba(18,48,109,.78),rgba(4,12,31,.92)); border: 1px solid rgba(245,197,66,.42); border-radius: 14px; padding: 1rem; }
        [data-testid="stMetricValue"] { color: var(--gh-gold) !important; }
        [data-testid="stMetricLabel"] { color: #ffffff !important; font-weight: 800 !important; opacity: 1 !important; }
        [data-testid="stDataFrame"] { border: 1px solid rgba(245,197,66,.38); border-radius: 12px; overflow: hidden; }
.gh-hero { position: relative; overflow: hidden; min-height: 260px; border: 1px solid rgba(245,197,66,.48); border-radius: 16px; box-shadow: 0 0 32px rgba(89,55,180,.28), inset 0 0 32px rgba(0,0,0,.35); margin: .35rem 0 1.1rem; background: #020817; }
.gh-hero img { display: block; width: 100%; height: auto; object-fit: contain; }
.gh-dashboard-title { position: absolute; top: clamp(1rem,4vw,3rem); right: clamp(1rem,4vw,4rem); max-width: 54%; color: #fff6c9; font-family: Georgia, serif; font-size: clamp(1.55rem,3.4vw,4rem); font-weight: 900; line-height: 1.04; text-align: right; letter-spacing: .025em; text-shadow: 0 3px 18px #000, 0 0 24px rgba(245,197,66,.62); }
.gh-network { position: absolute; left: clamp(1rem,3vw,3rem); bottom: clamp(.8rem,2.2vw,2rem); color: #fff0ae; font-size: clamp(.72rem,1.3vw,1.2rem); font-weight: 900; letter-spacing: .13em; text-shadow: 0 2px 12px #000; }
[data-testid="stTabs"] [data-baseweb="tab-list"] { gap: .45rem; flex-wrap: wrap; }
[data-testid="stTabs"] [data-baseweb="tab"] { color: #ffffff !important; background: rgba(7, 26, 58, .78); border: 1px solid rgba(255, 217, 90, .38); border-radius: 8px 8px 0 0; font-size: 1.24rem !important; font-weight: 900 !important; letter-spacing: .025em; padding: .7rem 1.05rem !important; text-shadow: 0 1px 8px #000; }
[data-testid="stTabs"] [aria-selected="true"] { color: #fff4a8 !important; background: rgba(20, 61, 117, .92); border-bottom-color: #ffd95a !important; box-shadow: inset 0 -3px #ffd95a; }
[data-testid="stTabs"] [role="tab"], [data-testid="stTabs"] [role="tab"] * { color: #fffbe8 !important; opacity: 1 !important; }
[data-testid="stTabs"] [role="tab"][aria-selected="true"], [data-testid="stTabs"] [role="tab"][aria-selected="true"] * { color: #ffd95a !important; }

        .gh-scout-hero { margin: .6rem 0 1rem; padding: 1.25rem 1.4rem; border: 1px solid rgba(245,197,66,.48); border-radius: 16px; background: linear-gradient(125deg, rgba(18,48,109,.94), rgba(61,28,114,.86)); box-shadow: 0 0 28px rgba(111,69,202,.22); }
        .gh-scout-hero-title { color: #fff4b0; font-size: 1.5rem; font-weight: 900; letter-spacing: .04em; margin: 0; text-shadow: 0 2px 12px #000; }
        .gh-scout-hero-copy { color: #f3f7ff; margin: .35rem 0 0; font-size: 1rem; }
        .gh-scout-manager-label { color: #f5c542; font-weight: 900; letter-spacing: .12em; text-align: center; text-transform: uppercase; margin: .4rem 0 .15rem; }
        .gh-scout-table { border: 1px solid rgba(245,197,66,.35); border-radius: 14px; overflow-x: auto; background: rgba(3,8,23,.82); padding: .3rem; }
        .gh-scout-table table { width: 100%; border-collapse: collapse; color: #f3f7ff; font-size: .88rem; }
        .gh-scout-table th { color: #fff1a5; background: rgba(21,61,117,.85); text-align: left; padding: .7rem .55rem; white-space: nowrap; }
        .gh-scout-table td { border-top: 1px solid rgba(245,197,66,.18); padding: .55rem; vertical-align: top; }
        .gh-scout-table tr:nth-child(even) td { background: rgba(31,58,110,.20); }

        [data-testid="stExpander"], [data-testid="stFileUploader"], [data-testid="stForm"], [data-testid="stAlert"] { background: linear-gradient(145deg,rgba(18,48,109,.72),rgba(4,12,31,.92)) !important; border: 1px solid rgba(245,197,66,.32) !important; border-radius: 12px !important; color: #f5f8ff !important; }
        [data-testid="stFileUploader"] section { background: rgba(3,8,23,.75) !important; border: 1px dashed rgba(245,197,66,.52) !important; }
        [data-testid="stFileUploader"] small, [data-testid="stAlert"] * { color: #eef4ff !important; }
        [data-testid="stDataFrame"] { background: #071a3a !important; border: 1px solid rgba(245,197,66,.48) !important; border-radius: 12px !important; padding: .15rem !important; }
        [data-testid="stDataFrame"] > div, [data-testid="stDataFrame"] [role="grid"], [data-testid="stDataFrame"] canvas { background-color: #071a3a !important; }
        [data-baseweb="select"] > div, [data-baseweb="input"] > div, [data-testid="stTextInput"] input { background: #071a3a !important; color: #fff !important; border-color: rgba(245,197,66,.48) !important; }
        [data-baseweb="select"] * { color: #fff !important; }
        [data-testid="stTextInput"] div[data-baseweb="base-input"], [data-testid="stTextInput"] input,
        [data-testid="stSelectbox"] div[data-baseweb="select"] > div { background-color: #0b3474 !important; color: #ffffff !important; }
        [data-testid="stTextInput"] input:focus, [data-testid="stSelectbox"] div[data-baseweb="select"] > div:focus-within { border-color: #ffe36a !important; box-shadow: 0 0 0 2px rgba(255,227,106,.42) !important; }
        [data-baseweb="popover"], [data-baseweb="popover"] > div,
        [data-baseweb="menu"], [role="listbox"] { background: #071f4f !important; color: #ffffff !important; }
        [role="option"], [role="option"] *, [data-baseweb="menu"] li, [data-baseweb="menu"] li * { color: #ffffff !important; background-color: #071f4f !important; opacity: 1 !important; }
        [role="option"]:hover, [role="option"][aria-selected="true"], [role="option"][aria-selected="true"] * { background-color: #1555a5 !important; color: #ffffff !important; font-weight: 900 !important; }
        [data-testid="stSelectbox"] svg, [data-testid="stMultiSelect"] svg,
        [data-baseweb="select"] svg, [data-baseweb="select"] [role="button"] svg { fill: #ffffff !important; color: #ffffff !important; }
        [data-testid="stMultiSelect"] [data-baseweb="select"] > div,
        [data-testid="stMultiSelect"] [data-baseweb="tag"] { background-color: #0b3474 !important; color: #ffffff !important; border-color: rgba(245,197,66,.48) !important; }
        [data-testid="stMultiSelect"] [data-baseweb="tag"] * { color: #ffffff !important; fill: #ffffff !important; }
        [data-testid="stSelectbox"] input, [data-testid="stMultiSelect"] input { color: #ffffff !important; caret-color: #ffffff !important; }
        [data-testid="stButton"] button { background: linear-gradient(135deg,#133b80,#512d92) !important; color: #fff6c9 !important; border: 1px solid rgba(245,197,66,.62) !important; font-weight: 800 !important; }
        [data-testid="stButton"] button:hover { border-color: #ffe892 !important; box-shadow: 0 0 15px rgba(245,197,66,.35) !important; }
        [data-testid="stMarkdownContainer"] hr { border-color: rgba(245,197,66,.32) !important; }

        .stApp, [data-testid="stMarkdownContainer"], [data-testid="stDataFrame"], [data-testid="stDataFrame"] *, [data-testid="stFileUploader"], [data-testid="stFileUploader"] *, [data-baseweb="select"], [data-baseweb="select"] *, [data-testid="stTextInput"] input, [data-testid="stButton"] button { font-size: 1.06rem !important; }
        [data-testid="stMetricLabel"] { font-size: 1.02rem !important; }
        [data-testid="stMetricValue"] { font-size: 2.15rem !important; }
        /* Creator data boxes: larger numeric values, roomier rows, and a usable read area. */
        [data-testid="stDataFrame"] { min-height: 520px !important; padding: .65rem !important; }
        [data-testid="stDataFrame"] [role="columnheader"], [data-testid="stDataFrame"] [role="columnheader"] * { font-size: 1.2rem !important; font-weight: 800 !important; }
        [data-testid="stDataFrame"] [role="gridcell"], [data-testid="stDataFrame"] [role="gridcell"] * { font-size: 1.18rem !important; line-height: 1.45 !important; }
        .gh-scout-table { font-size: 1.18rem !important; }
        .gh-scout-table th { padding: 1rem .9rem !important; font-size: 1.22rem !important; }
        .gh-scout-table td { padding: .95rem .9rem !important; line-height: 1.45 !important; }
        .gh-scout-table td:not(:first-child) { color: #ffe36a !important; font-size: 1.3rem !important; font-weight: 900 !important; letter-spacing: .01em; text-shadow: 0 0 10px rgba(245,197,66,.28); }
        .gh-scout-table td:first-child { color: #ffffff !important; font-weight: 800 !important; white-space: pre-line !important; min-width: 190px; }
        [data-testid="stMetricValue"] { font-size: clamp(4.6rem, 7vw, 6.4rem) !important; line-height: .98 !important; font-weight: 900 !important; letter-spacing: .01em; text-shadow: 0 0 24px rgba(245,197,66,.62); }
        .gh-data-panel { min-height: 470px; overflow: auto; border: 1px solid rgba(245,197,66,.55); border-radius: 14px; background: linear-gradient(145deg, rgba(18,48,109,.92), rgba(3,8,23,.96)); box-shadow: inset 0 0 28px rgba(0,0,0,.28); }
        .gh-data-panel table { width: 100%; border-collapse: collapse; color: #f5f8ff; font-size: 1.0rem; }
        .gh-data-panel th { position: sticky; top: 0; z-index: 1; padding: 1rem .9rem; text-align: left; background: #12376f; color: #fff3ad; font-size: 1.06rem; font-weight: 900; white-space: nowrap; border-bottom: 2px solid rgba(245,197,66,.55); }
        .gh-data-panel td { padding: 1rem .9rem; line-height: 1.45; border-top: 1px solid rgba(245,197,66,.18); vertical-align: top; }
        .gh-data-panel tr:nth-child(even) td { background: rgba(42,75,140,.24); }
        .gh-data-panel td:not(:first-child) { color: #ffe36a; font-size: 1.08rem; font-weight: 800; text-shadow: 0 0 10px rgba(245,197,66,.24); }
        .gh-data-panel td:first-child { color: #ffffff; font-size: 1.04rem; font-weight: 750; }
        .gh-data-panel { scrollbar-color: #ffd95a #071a3a; scrollbar-width: auto; }
        .gh-data-panel::-webkit-scrollbar { width: 18px; height: 18px; }
        .gh-data-panel::-webkit-scrollbar-track { background: #071a3a; border-left: 1px solid rgba(245,197,66,.42); }
        .gh-data-panel::-webkit-scrollbar-thumb { background: linear-gradient(#ffe889,#b87d13); border: 3px solid #071a3a; border-radius: 12px; min-height: 54px; }
        .gh-data-panel::-webkit-scrollbar-thumb:hover { background: #fff2a8; }
        .gh-data-panel:focus { outline: 3px solid #ffe889; outline-offset: 3px; }
        .gh-data-panel td { cursor: pointer; }
        /* Goal Management controls: make Manager and Search labels readable on the dark layout. */
        [data-testid="stTextInput"] label, [data-testid="stTextInput"] label *, [data-testid="stSelectbox"] label, [data-testid="stSelectbox"] label *, [data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] * { color: #fff4b0 !important; opacity: 1 !important; font-size: 1.18rem !important; font-weight: 900 !important; text-shadow: 0 1px 7px #000; }
        [data-testid="stTextInput"] input { color: #ffffff !important; font-weight: 700 !important; }
        [data-testid="stTextInput"] input::placeholder { color: #dceaff !important; opacity: 1 !important; }
        [data-testid="stSelectbox"] [role="combobox"], [data-testid="stSelectbox"] [role="combobox"] * { color: #ffffff !important; font-weight: 800 !important; }
        /* Main dashboard selector: keep Agency/manager selection unmistakably visible. */
        .st-key-dashboard_manager_filter label, .st-key-dashboard_manager_filter label * { color: #fff4b0 !important; opacity: 1 !important; font-size: 1.25rem !important; font-weight: 900 !important; text-shadow: 0 1px 8px #000; }
        .st-key-dashboard_manager_filter [data-baseweb="select"] > div { background: #12376f !important; border: 2px solid #ffd95a !important; }
        .st-key-dashboard_manager_filter [data-baseweb="select"] [role="combobox"], .st-key-dashboard_manager_filter [data-baseweb="select"] [role="combobox"] * { color: #ffffff !important; font-size: 1.2rem !important; font-weight: 900 !important; }
        /* The number itself inside each dashboard summary card. */
        [data-testid="stMetricValue"], [data-testid="stMetricValue"] > div, [data-testid="stMetricValue"] div, [data-testid="stMetricValue"] span, [data-testid="stMetricValue"] p { font-size: clamp(2.2rem, 3.1vw, 2.7rem) !important; line-height: 1.05 !important; color: #ffe36a !important; font-weight: 900 !important; text-shadow: 0 0 12px rgba(245,197,66,.42) !important; }
        955

                /* Keep colored data cards; restore normal typography. */
        .stApp, [data-testid="stMarkdownContainer"], [data-testid="stDataFrame"] * { font-size: 1rem !important; }
        [data-testid="stMetricValue"], [data-testid="stMetricValue"] * { font-size: 2rem !important; line-height: 1.08 !important; }
        [data-testid="stMetricLabel"], [data-testid="stMetricLabel"] * { font-size: .9rem !important; font-weight: 700 !important; }
        [data-testid="stDataFrame"] [role="columnheader"] *, [data-testid="stDataFrame"] [role="gridcell"] *, .gh-data-panel table, .gh-data-panel th, .gh-data-panel td, .gh-scout-table, .gh-scout-table th, .gh-scout-table td { font-size: 1rem !important; line-height: 1.3 !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="gh-hero"><img src="{banner_data_uri()}" alt="Grace Harbour lighthouse and water"><div class="gh-dashboard-title">TikTok Live<br>Manager Dashboard</div><div class="gh-network">⚓ GRACE HARBOUR MEDIA &nbsp;•&nbsp; CREATOR NETWORK</div></div>',
        unsafe_allow_html=True,
    )








    try:
        # The data tables are provisioned by the importer. Do not run DDL during
        # a visitor request: it can block a Streamlit session behind a database lock.
        managers = load_goal_managers()
        creators = load_goal_creators()
        business_source = load_business_essentials()
        access_people = load_access_people()
        monthly_metrics = load_monthly_metrics()
    except Exception as exc:
        print(f"DASHBOARD_BOOT_FAILURE class={type(exc).__name__} sqlstate={getattr(getattr(exc, 'orig', None), 'sqlstate', None)} detail={exc}")
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








    manager_tab, goals_tab, business_tab, maintenance_tab, scouting_tab, prior_month_tab, tier_guide_tab, access_tab = st.tabs([
        "Dashboard",
        "Goal Management",
        "Business Essentials",
        "Maintenance Rate",
        "Scouting",
        "Goal Management Prior Month",
        "Tier & Level Guide",
        "Access & Data",
    ])








    with manager_tab:
        st.caption("A combined view of the latest Goal Management and Business Essentials measures."); manager_logo_files = {"agency":"agency-logo.png","chersade":"cher.jpg","ladykmo":"ladykmo.jpg","glittersunfun":"glittersunfun.jpg","joedickerson":"joe-dickerson.jpg","leslieclark":"leslie-clark.jpg","oglittlesouthernguyandgal":"og-little-southern-guy-and-gal.jpg","pap":"pap.jpg","tonipeters":"toni-peters.jpg","lacie":"lacie.jpg","ariana":"ariana.jpg","arianasahm":"ariana.jpg","amazinggrace":"amazinggrace.jpg","amazinggraceof3":"amazinggrace.jpg"}; dashboard_manager_choices = ["Agency", *[m for m in sorted(creators.get("manager", pd.Series(dtype=str)).dropna().astype(str).unique().tolist()) if m.strip() and m.strip() != "-"]]; dashboard_choice = st.session_state.get("dashboard_manager_filter", "Agency"); _manager_logo_key = "".join(c for c in dashboard_choice.lower() if c.isalnum()); _manager_logo_name = manager_logo_files.get(_manager_logo_key); _selected_logo_path = (__import__("pathlib").Path(__file__).resolve().parent / "assets" / ("agency-logo.png" if dashboard_choice == "Agency" else "manager-logos") / ("" if dashboard_choice == "Agency" else _manager_logo_name)) if _manager_logo_name else None; _manager_logo_mime = "png" if _selected_logo_path and _selected_logo_path.suffix.lower() == ".png" else "jpeg"; _manager_logo_render = None
        _dashboard_logo_left, _dashboard_logo_center, _dashboard_logo_right = st.columns([1, 1, 1])
        with _dashboard_logo_center:
            if _selected_logo_path and _selected_logo_path.exists():
                st.image(str(_selected_logo_path), width=180)
        dashboard_choice = st.selectbox("Dashboard view", dashboard_manager_choices, key="dashboard_manager_filter")
        if creators.empty:
            st.info("No Goal Management records have been imported yet.")
        else:
            dashboard_creators = creators.copy(); dashboard_creators = dashboard_creators[dashboard_creators["_manager"] == dashboard_choice].copy() if dashboard_choice != "Agency" else dashboard_creators; dashboard_managers = managers[managers["_manager"] == dashboard_choice].copy() if dashboard_choice != "Agency" and not managers.empty else managers.copy()
            dashboard_diamonds = numeric_series(dashboard_creators, "diamonds")
            dashboard_tier = dashboard_creators.get("tier_status", pd.Series("", index=dashboard_creators.index)).fillna("").astype(str).str.lower()
            dashboard_rank = dashboard_creators.get("rank_up_progress", pd.Series("", index=dashboard_creators.index)).fillna("").astype(str).str.lower()
            dashboard_maintained = dashboard_tier.str.contains("maintained|maintain", na=False) | dashboard_rank.str.contains("maintain", na=False)
            dashboard_ranked = dashboard_tier.str.contains("ranked up|ranking up", na=False) | dashboard_rank.str.contains("rank up|ranked up", na=False)
            dashboard_not_maintained = dashboard_tier.str.contains("not maintained|not maintain", na=False) | dashboard_rank.str.contains("not maintained|not maintain", na=False)
            dashboard_new_creators = monthly_metric_value(monthly_metrics, "new_creators", int(numeric_series(dashboard_managers, "new_creators").sum()) if not dashboard_managers.empty else 0)
            if dashboard_choice == "Agency":
                with st.container(border=True):
                    st.subheader("Agency Focus Goals")
                    focus_total_goal = monthly_metric_value(monthly_metrics, "total_diamond_goal", 0)
                    focus_minimum_goal = monthly_metric_value(monthly_metrics, "minimum_diamond_goal", 0)
                    focus_prior_diamonds = monthly_metric_value(monthly_metrics, "prior_month_diamonds", 0)
                    focus_current_diamonds = int(dashboard_diamonds.sum())
                    focus_today = pd.Timestamp.now(tz="America/New_York")
                    # TikTok's reporting month starts at 8:00 PM ET on the last
                    # calendar day of the prior month and ends just before 8:00
                    # PM ET on the last calendar day of the current month. The
                    # completed month remains displayed until the first new
                    # reporting-day value is available at 8:00 PM ET on day 1.
                    focus_reporting_today = focus_today
                    if focus_today.day == 1 and focus_today.hour < 20:
                        focus_reporting_today = focus_today - pd.offsets.MonthBegin(1)
                    focus_month_start = focus_reporting_today.normalize().replace(day=1)
                    focus_next_month_start = focus_month_start + pd.offsets.MonthBegin(1)
                    focus_reporting_start = focus_month_start - pd.Timedelta(hours=4)
                    focus_reporting_end = focus_next_month_start - pd.Timedelta(hours=4)
                    focus_total_reporting_days = int((focus_reporting_end - focus_reporting_start).total_seconds() / 86_400)
                    focus_elapsed_reporting_days = min(focus_total_reporting_days, max(1, focus_reporting_today.day))
                    focus_projected_diamonds = (
                        focus_current_diamonds
                        / focus_elapsed_reporting_days
                        * focus_total_reporting_days
                    ) if focus_current_diamonds else 0.0
                    if focus_projected_diamonds >= focus_total_goal > 0:
                        focus_diamond_color = "#40e39a"
                        focus_diamond_status = "Pacing to total goal"
                    elif focus_projected_diamonds >= focus_minimum_goal > 0:
                        focus_diamond_color = "#ffd166"
                        focus_diamond_status = "Pacing above minimum goal"
                    else:
                        focus_diamond_color = "#ff5c7a"
                        focus_diamond_status = "Pacing below minimum goal"

                    focus_maintenance_rate = monthly_metric_float(monthly_metrics, "maintenance_rate", 0.0)
                    try:
                        focus_maintenance_payloads = pd.read_sql(text("SELECT payload FROM maintenance_rate_rows ORDER BY row_index"), get_engine()) if not focus_maintenance_rate else pd.DataFrame()
                        if not focus_maintenance_rate and not focus_maintenance_payloads.empty:
                            focus_maintenance_data = pd.DataFrame(focus_maintenance_payloads["payload"].tolist())
                            if not focus_maintenance_data.empty and "maintained_tier" in focus_maintenance_data.columns:
                                focus_maintenance_rate = float(focus_maintenance_data["maintained_tier"].fillna(False).astype(bool).mean() * 100)
                    except Exception:
                        focus_maintenance_rate = 0.0
                    if focus_maintenance_rate >= 50:
                        focus_maintenance_color = "#40e39a"
                        focus_maintenance_status = "Goal achieved"
                    elif focus_maintenance_rate >= 45:
                        focus_maintenance_color = "#ffd166"
                        focus_maintenance_status = "Close to goal"
                    else:
                        focus_maintenance_color = "#ff5c7a"
                        focus_maintenance_status = "Below 45%"

                    focus_graduation_rate = 0.0
                    focus_reached_count = 0
                    focus_graduation_total = 0
                    if not business.empty:
                        focus_business_sections = business.get("Section", pd.Series("", index=business.index)).astype(str)
                        focus_graduation_rows = business[
                            focus_business_sections.str.contains("Creator Graduation", case=False, na=False)
                            & focus_business_sections.str.contains("Evaluated", case=False, na=False)
                        ]
                        focus_reached_rows = business[focus_business_sections.str.contains("Reached graduation", case=False, na=False)]
                        focus_reached_count = int(
                            focus_reached_rows.get("Reached graduation", pd.Series(dtype="object"))
                            .astype(str).str.casefold().eq("yes").sum()
                        )
                        focus_graduation_total = len(focus_graduation_rows)
                        if focus_graduation_total:
                            focus_graduation_rate = focus_reached_count / focus_graduation_total * 100
                    focus_graduation_goal = (focus_graduation_total * 15 + 99) // 100
                    focus_graduation_needed = max(0, focus_graduation_goal - focus_reached_count)
                    if focus_graduation_rate >= 15:
                        focus_graduation_color = "#40e39a"
                        focus_graduation_status = "Goal achieved"
                    elif focus_graduation_rate >= 10:
                        focus_graduation_color = "#ffd166"
                        focus_graduation_status = "Close to goal"
                    else:
                        focus_graduation_color = "#ff5c7a"
                        focus_graduation_status = "Below 10%"

                    # The combined maintenance/rank-up rate is based on the creators
                    # evaluated in the current Goal Management dataset. The live
                    # Manage Creators count remains a separate operational metric.
                    focus_creator_total = len(dashboard_creators)
                    focus_active_creator_total = monthly_metric_value(monthly_metrics, "active_creators", 257)
                    focus_creator_half_goal = (focus_creator_total + 1) // 2
                    focus_maintaining_or_ranked = dashboard_maintained | dashboard_ranked
                    focus_maintaining_or_ranked_count = int(focus_maintaining_or_ranked.sum())
                    focus_maintaining_or_ranked_pct = (focus_maintaining_or_ranked_count / focus_creator_total * 100) if focus_creator_total else 0.0
                    focus_maintaining_needed = max(focus_creator_half_goal - focus_maintaining_or_ranked_count, 0)

                    def percentage_status(rate):
                        if rate >= 50:
                            return "#40e39a", "Goal achieved"
                        if rate >= 45:
                            return "#ffd166", "Close to goal"
                        return "#ff5c7a", "Below 45%"

                    focus_combined_color, focus_combined_status = percentage_status(focus_maintaining_or_ranked_pct)

                    def focus_card(column, label, value, color="#f5c542", detail=""):
                        column.markdown(
                            f"<div style='min-height:132px;padding:16px 18px;border-radius:14px;border:1px solid rgba(245,197,66,.32);background:linear-gradient(145deg,rgba(20,29,65,.96),rgba(7,11,30,.96));box-shadow:0 8px 22px rgba(0,0,0,.22)'>"
                            f"<div style='font-size:.84rem;font-weight:750;letter-spacing:.04em;color:#dce5ff'>{label}</div>"
                            f"<div style='font-size:2rem;line-height:1.15;margin-top:8px;font-weight:900;color:{color}'>{value}</div>"
                            f"<div style='margin-top:9px;font-size:1rem;line-height:1.35;font-weight:750;color:#dce5ff'>{detail}</div></div>",
                            unsafe_allow_html=True,
                        )

                    focus_one, focus_two, focus_three = st.columns(3)
                    focus_card(
                        focus_one,
                        "Current Diamonds",
                        f"{focus_current_diamonds:,}",
                        focus_diamond_color,
                        f"{focus_diamond_status} • Projected month-end {focus_projected_diamonds:,.2f}",
                    )
                    focus_card(focus_two, "Minimum Diamond Goal", f"{focus_minimum_goal:,}", "#f5c542", "15% above prior month")
                    focus_card(focus_three, "Total Diamond Goal", f"{focus_total_goal:,}", "#f5c542", "Agency monthly target")
                    focus_four, focus_five = st.columns(2)
                    focus_card(focus_four, "Maintenance Level", f"{focus_maintenance_rate:.2f}%", focus_maintenance_color, f"{focus_maintenance_status} • Green at 50%")
                    focus_card(focus_five, "Current Graduation Rate", f"{focus_graduation_rate:.2f}%", focus_graduation_color, f"{focus_reached_count:,} current • 15% goal {focus_graduation_goal:,} • Need {focus_graduation_needed:,} more")
                    focus_six, focus_seven = st.columns(2)
                    focus_card(focus_six, "Maintaining or Ranking Up", f"{focus_maintaining_or_ranked_pct:.2f}%", focus_combined_color, f"{focus_maintaining_or_ranked_count:,} of {focus_creator_total:,} • Need {focus_maintaining_needed:,} more • 50% goal ({focus_creator_half_goal:,})")
                    focus_card(focus_seven, "Active Creators", f"{focus_active_creator_total:,}", "#f5c542", "Live count from Manage creators • Updates with Goals")
                    st.caption(
                        f"Diamond projection uses {focus_elapsed_reporting_days} of "
                        f"{focus_total_reporting_days} reporting days (8:00 PM ET month boundary). "
                        f"Goal baseline: prior month diamonds {focus_prior_diamonds:,}. "
                        "Manager views show their own focus results."
                    )
            else:
                manager_creator_total = len(dashboard_creators)
                manager_half_goal = (manager_creator_total + 1) // 2
                manager_maintaining_or_ranked = dashboard_maintained | dashboard_ranked
                manager_maintaining_or_ranked_count = int(manager_maintaining_or_ranked.sum())
                manager_maintaining_or_ranked_pct = (manager_maintaining_or_ranked_count / manager_creator_total * 100) if manager_creator_total else 0.0

                manager_business = business.copy()
                if not manager_business.empty and "Manager" in manager_business.columns:
                    manager_business = manager_business[manager_business["Manager"].fillna("").astype(str) == dashboard_choice].copy()
                elif not manager_business.empty:
                    manager_business = manager_business.iloc[0:0].copy()
                manager_sections = manager_business.get("Section", pd.Series("", index=manager_business.index)).astype(str)
                manager_graduation_rows = manager_business[
                    manager_sections.str.contains("Creator Graduation", case=False, na=False)
                    & manager_sections.str.contains("Evaluated", case=False, na=False)
                ]
                manager_reached_rows = manager_business[manager_sections.str.contains("Reached graduation", case=False, na=False)]
                manager_reached_count = int(
                    manager_reached_rows.get("Reached graduation", pd.Series(dtype="object"))
                    .astype(str).str.casefold().eq("yes").sum()
                )
                manager_graduation_total = len(manager_graduation_rows)
                manager_graduation_goal = (manager_graduation_total * 15 + 99) // 100
                manager_graduation_needed = max(0, manager_graduation_goal - manager_reached_count)
                manager_graduation_pct = (manager_reached_count / manager_graduation_total * 100) if manager_graduation_total else 0.0

                def manager_status(rate):
                    if rate >= 50:
                        return "#40e39a", "Goal achieved"
                    if rate >= 45:
                        return "#ffd166", "Close to goal"
                    return "#ff5c7a", "Below 45%"

                def manager_focus_card(column, label, value, color, detail):
                    column.markdown(
                        f"<div style='min-height:132px;padding:16px 18px;border-radius:14px;border:1px solid rgba(245,197,66,.32);background:linear-gradient(145deg,rgba(20,29,65,.96),rgba(7,11,30,.96));box-shadow:0 8px 22px rgba(0,0,0,.22)'>"
                        f"<div style='font-size:.84rem;font-weight:750;letter-spacing:.04em;color:#dce5ff'>{label}</div>"
                        f"<div style='font-size:2rem;line-height:1.15;margin-top:8px;font-weight:900;color:{color}'>{value}</div>"
                        f"<div style='margin-top:9px;font-size:1rem;line-height:1.35;font-weight:750;color:#dce5ff'>{detail}</div></div>",
                        unsafe_allow_html=True,
                    )

                manager_combined_color, manager_combined_status = manager_status(manager_maintaining_or_ranked_pct)
                if manager_graduation_pct >= 15:
                    manager_graduation_color, manager_graduation_status = "#40e39a", "Goal achieved"
                elif manager_graduation_pct >= 10:
                    manager_graduation_color, manager_graduation_status = "#ffd166", "Close to goal"
                else:
                    manager_graduation_color, manager_graduation_status = "#ff5c7a", "Below 10%"
                with st.container(border=True):
                    st.subheader(f"{dashboard_choice} Focus Goals")
                    manager_one, manager_two = st.columns(2)
                    manager_focus_card(manager_one, "Maintaining or Ranking Up", f"{manager_maintaining_or_ranked_pct:.2f}%", manager_combined_color, f"50% goal • {manager_maintaining_or_ranked_count:,} unique creators • Need {manager_half_goal:,} of {manager_creator_total:,}")
                    manager_focus_card(manager_two, "Graduation Rate", f"{manager_graduation_pct:.2f}%", manager_graduation_color, f"{manager_reached_count:,} current • Goal {manager_graduation_goal:,} • Need {manager_graduation_needed:,} more")
                    st.caption("Manager-specific results. Maintaining/Ranking Up uses 45%/50% thresholds; Graduation uses 10%/15% thresholds.")

            st.subheader("Goal Management overview")
            a, b, c, d = st.columns(4)
            a.metric("Creators", f"{len(dashboard_creators):,}")
            b.metric("Diamonds", f"{int(dashboard_diamonds.sum()):,}")
            c.metric("New creators", f"{dashboard_new_creators:,}")
            d.metric("Above 200k diamonds", f"{int(dashboard_diamonds.ge(200_000).sum()):,}")
            e, f, g = st.columns(3)
            e.metric("Maintaining tier", f"{int(dashboard_maintained.sum()):,}")
            f.metric("Ranking up", f"{int(dashboard_ranked.sum()):,}")
            g.metric("Tier not maintained", f"{int(dashboard_not_maintained.sum()):,}")

            st.subheader("Maintenance Rate overview")
            dashboard_maintenance = pd.DataFrame()
            try:
                dashboard_maintenance_payloads = pd.read_sql(text("SELECT payload FROM maintenance_rate_rows ORDER BY row_index"), get_engine())
                if not dashboard_maintenance_payloads.empty:
                    dashboard_maintenance = pd.DataFrame(dashboard_maintenance_payloads["payload"].tolist())
            except Exception:
                dashboard_maintenance = pd.DataFrame()

            if not dashboard_maintenance.empty and "maintained_tier" in dashboard_maintenance.columns:
                dashboard_maintenance_total = len(dashboard_maintenance)
                dashboard_maintaining_count = int(dashboard_maintenance["maintained_tier"].fillna(False).astype(bool).sum())
                dashboard_maintenance_rate = (dashboard_maintaining_count / dashboard_maintenance_total * 100) if dashboard_maintenance_total else 0.0
                maintenance_one, maintenance_two, maintenance_three = st.columns(3)
                maintenance_one.metric("Creators in Maintenance Rate", f"{dashboard_maintenance_total:,}")
                maintenance_two.metric("Maintaining or ranked up", f"{dashboard_maintaining_count:,}")
                maintenance_three.metric("Maintenance rate", f"{dashboard_maintenance_rate:.2f}%")
            else:
                st.info("Maintenance Rate data is waiting for its next complete read.")
        if business.empty:
            st.info("No Business Essentials records have been imported yet.")
        else:
            dashboard_business = business.copy(); dashboard_business = dashboard_business[dashboard_business["Manager"].fillna("").astype(str) == dashboard_choice].copy() if dashboard_choice != "Agency" and "Manager" in dashboard_business.columns else dashboard_business
            dashboard_sections = dashboard_business.get("Section", pd.Series("", index=dashboard_business.index)).astype(str)
            dashboard_stability = dashboard_business[dashboard_sections.str.contains("Creator Stability", case=False, na=False)].copy()
            dashboard_graduation = dashboard_business[dashboard_sections.str.contains("Creator Graduation", case=False, na=False) & dashboard_sections.str.contains("Evaluated", case=False, na=False)].copy()
            dashboard_reached = dashboard_business[dashboard_sections.str.contains("Reached graduation", case=False, na=False)].copy()
            dashboard_reward = dashboard_business[dashboard_sections.str.contains("Extra Reward", case=False, na=False)].copy()
            dashboard_new = int(dashboard_graduation.get("New creator this month", pd.Series(dtype="object")).astype(str).str.casefold().eq("yes").sum())
            dashboard_quit = int(dashboard_stability.get("Voluntary quit", pd.Series(dtype="object")).astype(str).str.casefold().eq("yes").sum())
            dashboard_reached_count = int(dashboard_reached.get("Reached graduation", pd.Series(dtype="object")).astype(str).str.casefold().eq("yes").sum())
            dashboard_reward_completed = int(dashboard_reward.get("Completed rank-up incentive", pd.Series(dtype="object")).astype(str).str.casefold().eq("yes").sum())
            st.subheader("Business Essentials overview")
            h, i, j, k = st.columns(4)
            h.metric("Creator Stability - evaluated", f"{len(dashboard_stability):,}")
            i.metric("New creators this month", f"{dashboard_new:,}")
            j.metric("Creators quit", f"{dashboard_quit:,}")
            k.metric("Quit rate", f"{(dashboard_quit / len(dashboard_stability) * 100) if len(dashboard_stability) else 0:.2f}%")
            l, m, n, o = st.columns(4)
            l.metric("Reached graduation", f"{dashboard_reached_count:,}")
            m.metric("Graduation rate", f"{(dashboard_reached_count / len(dashboard_graduation) * 100) if len(dashboard_graduation) else 0:.2f}%", delta="Goal: 15% minimum", delta_color="normal")
            n.metric("Premium Invite Graduates", f"{dashboard_reward_completed:,} / {len(dashboard_reward):,}")
            o.metric("Creators with Extra Reward", f"{len(dashboard_reward):,}")








    with goals_tab:
        st.caption("Current Creator-tab Goal Management records from the latest authorized Backstage capture.")
        if creators.empty:
            st.info("No creator-goal records have been imported yet.")
        else:
            goal_manager_choices = ["All managers", *manager_names]
            goal_manager_current = st.session_state.get("goal_management_manager_filter", "All managers")
            _goal_logo_key = "agency" if goal_manager_current == "All managers" else "".join(c for c in goal_manager_current.lower() if c.isalnum())
            _goal_logo_name = manager_logo_files.get(_goal_logo_key)
            _goal_logo_path = (Path(__file__).resolve().parent / "assets" / "agency-logo.png" if _goal_logo_key == "agency" else Path(__file__).resolve().parent / "assets" / "manager-logos" / _goal_logo_name) if _goal_logo_name else None
            _goal_logo_mime = "image/png" if _goal_logo_path and _goal_logo_path.suffix.lower() == ".png" else "image/jpeg"
            _goal_logo_left, _goal_logo_center, _goal_logo_right = st.columns([1, 1, 1])
            with _goal_logo_center:
                if _goal_logo_path and _goal_logo_path.exists():
                    st.image(str(_goal_logo_path), width=180)
            goal_filter_left, goal_filter_right = st.columns(2)
            goal_manager_choice = goal_filter_left.selectbox("Manager", goal_manager_choices, key="goal_management_manager_filter")
            tier_source = creators.get("tier_status", pd.Series("", index=creators.index)).fillna("").astype(str)
            tier_levels = (tier_source.str.extract(r"(?i)(tier\s*\d+)")[0].dropna().str.replace(r"\s+", " ", regex=True).str.title().drop_duplicates().sort_values().tolist())
            goal_tier_choice = goal_filter_right.selectbox("Tier level", ["All tiers", *tier_levels], key="goal_management_tier_filter")
            goal_creator_search = st.text_input("Search creators", placeholder="Type a creator name or username", key="goal_management_creator_search").strip()
            visible = creators.copy()
            if goal_manager_choice != "All managers":
                visible = visible[visible["_manager"] == goal_manager_choice].copy()
            if goal_tier_choice != "All tiers":
                visible_tiers = (visible.get("tier_status", pd.Series("", index=visible.index)).fillna("").astype(str).str.extract(r"(?i)(tier\s*\d+)")[0].fillna("").str.replace(r"\s+", " ", regex=True).str.title())
                visible = visible[visible_tiers == goal_tier_choice].copy()
            if goal_creator_search:
                searchable_creator = visible.get("username", pd.Series("", index=visible.index)).fillna("").astype(str)
                searchable_creator = searchable_creator + " " + visible.get("creator_id", pd.Series("", index=visible.index)).fillna("").astype(str)
                visible = visible[searchable_creator.str.contains(re.escape(goal_creator_search), case=False, na=False)].copy()
            visible_diamonds = numeric_series(visible, "diamonds")
            tier_text = visible.get("tier_status", pd.Series("", index=visible.index)).fillna("").astype(str).str.lower()
            rank_text = visible.get("rank_up_progress", pd.Series("", index=visible.index)).fillna("").astype(str).str.lower()
            ranked_up = int((tier_text.str.contains("rank") | rank_text.str.contains("rank")).sum())
            maintained = int((tier_text.str.contains("maintain") | rank_text.str.contains("maintain")).sum())
            above_200k = int((visible_diamonds >= 200000).sum())
            selected_manager_rows = managers if goal_manager_choice == "All managers" else managers[managers["_manager"] == goal_manager_choice]
            new_creators = monthly_metric_value(monthly_metrics, "new_creators", int(numeric_series(selected_manager_rows, "new_creators").sum()) if not selected_manager_rows.empty else 0)
            not_maintained_text = tier_text.str.contains("not maintained") | rank_text.str.contains("not maintained")
            
            ranked_mask = tier_text.str.contains("rank") | rank_text.str.contains("rank")
            maintained_mask = ~ranked_mask & ~not_maintained_text & (
                tier_text.str.contains("maintain") | rank_text.str.contains("maintain")
            )
            not_maintained_mask = not_maintained_text | ~(ranked_mask | maintained_mask)
            above_200k = int((visible_diamonds >= 200000).sum())
            selected_manager_rows = managers if goal_manager_choice == "All managers" else managers[managers["_manager"] == goal_manager_choice]
            new_creators = monthly_metric_value(monthly_metrics, "new_creators", int(numeric_series(selected_manager_rows, "new_creators").sum()) if not selected_manager_rows.empty else 0)

            first, second, third, fourth = st.columns(4)
            first.metric("Creators", f"{len(visible):,}")
            second.metric("Diamonds", f"{int(visible_diamonds.sum()):,}")
            third.metric("New creators", f"{new_creators:,}")
            fourth.metric("Above 200k diamonds", f"{above_200k:,}")

            fifth, sixth, seventh = st.columns(3)
            
        
            fifth.metric("Maintaining tier", f"{int(maintained_mask.sum()):,}")
            sixth.metric("Ranking up", f"{int(ranked_mask.sum()):,}")
            seventh.metric("Tier not maintained", f"{int(not_maintained_mask.sum()):,}")

            def creator_goal_display(frame, include_manager=False):
                output = pd.DataFrame({
                    "Creator": frame.get("username", frame.get("creator_id", pd.Series("", index=frame.index))),
                    "Diamonds": numeric_series(frame, "diamonds").astype("int64"),
                    "Valid go LIVE days": numeric_series(frame, "valid_live_days").astype("int64"),
                    "Valid LIVE duration": numeric_series(frame, "valid_live_hours"),
                    "Bonus contribution": numeric_series(frame, "estimated_bonus"),
                    "Tier": frame.get("tier_status", pd.Series("", index=frame.index)),
                    "Tier movement": frame.get("rank_up_progress", pd.Series("", index=frame.index)),
                    "Activeness": frame.get("activeness_level", pd.Series("", index=frame.index)),
                    "Live now": frame.get("live_now", pd.Series("", index=frame.index)),
                })
                if include_manager:
                    output.insert(1, "Manager", frame["_manager"].fillna("").astype(str).values)
                return output.sort_values("Diamonds", ascending=False)

            def goal_section(title, frame, empty_message, include_manager=False):
                with st.container(border=True):
                    st.subheader(title)
                    if frame.empty:
                        st.caption(empty_message)
                    else:
                        render_read_table(creator_goal_display(frame, include_manager=include_manager))

            selection_label = "all managers" if goal_manager_choice == "All managers" else goal_manager_choice
            st.caption(f"Showing every current Goal Management field for {selection_label}.")
            goal_section(
                "All creator goals",
                visible,
                "No creator-goal records match this selection.",
                include_manager=goal_manager_choice == "All managers",
            )
            goal_section(
                "Maintaining tier",
                visible[maintained_mask].copy(),
                "No creators are currently marked as maintaining tier.",
                include_manager=goal_manager_choice == "All managers",
            )
            goal_section(
                "Ranking up",
                visible[ranked_mask].copy(),
                "No creators are currently marked as ranking up.",
                include_manager=goal_manager_choice == "All managers",
            )
            goal_section(
                "Tier not maintained",
                visible[not_maintained_mask].copy(),
                "No creators are currently marked as not maintained.",
                include_manager=goal_manager_choice == "All managers",
            )


    with prior_month_tab:
        shared_prior = load_shared_prior_month()
        st.caption("A shared prior-month view for all dashboard visitors.")
        if shared_prior:
            shared_frame = pd.DataFrame(shared_prior["rows"])
            shared_columns = [column for column in shared_prior["columns"] if column in shared_frame.columns]
            st.subheader("Current shared prior-month view")
            st.caption(f"Source: {shared_prior['file_name']} · last published {shared_prior['uploaded_at']}")
            if shared_columns:
                manager_column = next((column for column in shared_columns if str(column).strip().lower() in {"manager", "manager name", "manager_name", "assigned manager", "creator network manager"}), None)
                if manager_column:
                    manager_values = shared_frame[manager_column].fillna("").astype(str).str.strip()
                    available_managers = sorted(value for value in manager_values.unique().tolist() if value)
                    prior_manager_choice = st.selectbox("Manager", ["All managers", *available_managers], key="prior_month_manager_filter")
                    display_frame = shared_frame if prior_manager_choice == "All managers" else shared_frame.loc[manager_values == prior_manager_choice]
                    st.caption(f"Showing {len(display_frame):,} prior-month records for {prior_manager_choice.lower() if prior_manager_choice == 'All managers' else prior_manager_choice}.")
                else:
                    display_frame = shared_frame
                    st.caption("This published file has no Manager column to filter yet.")
                render_read_table(display_frame[shared_columns], height=720)
            else:
                st.info("The shared file has no selected display columns yet.")
        else:
            st.info("No shared prior-month spreadsheet has been published yet.")

        st.divider()
        st.subheader("Publish a shared prior-month view")
        prior_file = st.file_uploader("Choose prior-month spreadsheet", type=["xlsx", "xls", "csv"], key="prior_month_file")
        if prior_file is not None:
            try:
                selected_sheet = ""
                if prior_file.name.lower().endswith(".csv"):
                    prior_data = pd.read_csv(prior_file)
                else:
                    workbook = pd.ExcelFile(prior_file)
                    selected_sheet = st.selectbox("Worksheet", workbook.sheet_names, key="prior_month_sheet")
                    prior_data = pd.read_excel(workbook, sheet_name=selected_sheet)
                available_columns = list(prior_data.columns)
                selected_columns = st.multiselect(
                    "Columns to include", available_columns, default=available_columns, key="prior_month_columns"
                )
                uploaded_manager_column = next((column for column in available_columns if str(column).strip().lower() in {"manager", "manager name", "manager_name", "assigned manager", "creator network manager"}), None)
                if uploaded_manager_column and uploaded_manager_column not in selected_columns:
                    selected_columns.append(uploaded_manager_column)
                if selected_columns:
                    render_read_table(prior_data[selected_columns])
                    if st.button("Publish shared prior-month view", type="primary"):
                        save_shared_prior_month(prior_file.name, selected_sheet, selected_columns, prior_data[selected_columns])
                        load_shared_prior_month.clear()
                        st.success("The shared prior-month view is published for everyone with dashboard access.")
                        st.rerun()
                else:
                    st.info("Choose one or more columns to display.")
            except Exception as error:
                st.error(f"That spreadsheet could not be read: {error}")








    with business_tab:
        st.caption("Business Essentials records from the latest complete Backstage capture.")
        if business.empty:
            st.info("No Business Essentials records have been imported yet.")
        else:
            visible_business = business.copy()
            if choice != "All managers" and "Manager" in visible_business.columns:
                visible_business = visible_business[visible_business["Manager"].fillna("").astype(str) == choice].copy()

            section_series = visible_business.get("Section", pd.Series("", index=visible_business.index)).astype(str)
            stability_rows = visible_business[section_series.str.contains("Creator Stability", case=False, na=False)].copy()
            graduation_rows = visible_business[section_series.str.contains("Creator Graduation", case=False, na=False) & section_series.str.contains("Evaluated", case=False, na=False)].copy()
            reached_rows = visible_business[section_series.str.contains("Reached graduation", case=False, na=False)].copy()
            reward_rows = visible_business[section_series.str.contains("Extra Reward", case=False, na=False)].copy()

            new_count = int(graduation_rows.get("New creator this month", pd.Series(dtype="object")).astype(str).str.casefold().eq("yes").sum())
            reached_count = int(reached_rows.get("Reached graduation", pd.Series(dtype="object")).astype(str).str.casefold().eq("yes").sum())
            quit_count = int(stability_rows.get("Voluntary quit", pd.Series(dtype="object")).astype(str).str.casefold().eq("yes").sum())
            extra_completed = int(reward_rows.get("Completed rank-up incentive", pd.Series(dtype="object")).astype(str).str.casefold().eq("yes").sum())

            live_days = pd.to_numeric(stability_rows.get("Valid go LIVE days", pd.Series(dtype="object")).fillna("").astype(str).str.extract(r"(\d+)")[0], errors="coerce").fillna(0)
            duration_parts = stability_rows.get("Valid LIVE duration", pd.Series(dtype="object")).fillna("").astype(str).str.extract(r"(?:(?P<hours>\d+)h)?\s*(?:(?P<minutes>\d+)m)?\s*(?:(?P<seconds>\d+)s)?").fillna(0).astype(float)
            total_live_hours = float((duration_parts["hours"] + duration_parts["minutes"] / 60 + duration_parts["seconds"] / 3600).sum()) if not duration_parts.empty else 0.0
            stability_pages = stability_rows["Source page"].nunique() if "Source page" in stability_rows.columns else 0
            graduation_pages = graduation_rows["Source page"].nunique() if "Source page" in graduation_rows.columns else 0
            reward_pages = reward_rows["Source page"].nunique() if "Source page" in reward_rows.columns else 0

            # Agency graduation focus list: hold a 165-creator minimum base, then follow the live evaluated count.
            evaluated_base = max(165, len(graduation_rows))
            graduation_target = (evaluated_base * 15 + 99) // 100
            focus_needed = max(0, graduation_target - reached_count)
            today_et = pd.Timestamp.now(tz="America/New_York")
            days_remaining = max(1, int(today_et.days_in_month - today_et.day + 1))
            progress_text = graduation_rows.get("Graduation progress", pd.Series("", index=graduation_rows.index)).fillna("").astype(str)
            current_diamonds = pd.to_numeric(
                progress_text.str.replace(",", "", regex=False).str.extract(r"(\d+)\s*/")[0],
                errors="coerce",
            ).fillna(0).astype("int64")
            already_graduated = current_diamonds.ge(200_000) | progress_text.str.contains("met target", case=False, na=False)
            focus_candidates = graduation_rows.loc[~already_graduated].copy()
            focus_candidates["_current_diamonds"] = current_diamonds.loc[focus_candidates.index]
            focus_candidates["_diamonds_remaining"] = (200_000 - focus_candidates["_current_diamonds"]).clip(lower=0)
            focus_candidates["_daily_goal"] = ((focus_candidates["_diamonds_remaining"] + days_remaining - 1) // days_remaining).astype("int64")
            quit_text = focus_candidates.get("Quit on", pd.Series("", index=focus_candidates.index)).fillna("").astype(str).str.strip()
            focus_candidates = focus_candidates[quit_text.isin(["", "-", "—", "None", "nan"])].copy()
            focus_candidates = focus_candidates.sort_values(["_daily_goal", "_diamonds_remaining", "_current_diamonds"], ascending=[True, True, False])

            with st.container(border=True):
                st.header("Graduation Focus Push List — 15% Goal")
                focus_one, focus_two, focus_three, focus_four = st.columns(4)
                focus_one.metric("Evaluated base", f"{evaluated_base:,}")
                focus_two.metric("Graduated", f"{reached_count:,} / {graduation_target:,}")
                focus_three.metric("Graduation rate", f"{(reached_count / len(graduation_rows) * 100) if len(graduation_rows) else 0:.2f}%")
                focus_four.metric("Focus creators needed", f"{focus_needed:,}")
                st.markdown(f"### Priority graduation push: Focus on these {focus_needed:,} creator(s) first. Daily diamond goals use the {days_remaining} remaining calendar day(s), including today.")
                if focus_needed == 0:
                    st.success("The 15% graduation goal is currently met.")
                elif focus_candidates.empty:
                    st.info("No active non-graduated creators are available for the focus list.")
                else:
                    focus_rows = focus_candidates.head(focus_needed)
                    focus_display = pd.DataFrame({
                        "Creator": focus_rows.get("Creator", pd.Series("", index=focus_rows.index)),
                        "Manager": focus_rows.get("Manager", pd.Series("", index=focus_rows.index)),
                        "Current diamonds": focus_rows["_current_diamonds"].map(lambda value: f"{int(value):,}"),
                        "Diamonds to 200K": focus_rows["_diamonds_remaining"].map(lambda value: f"{int(value):,}"),
                        "Days remaining": days_remaining,
                        "Daily diamond goal": focus_rows["_daily_goal"].map(lambda value: f"{int(value):,}"),
                    })
                    render_read_table(focus_display)

            one, two, three, four = st.columns(4)
            one.metric("Creator Stability — evaluated", f"{len(stability_rows):,}")
            two.metric("New creators this month", f"{new_count:,}")
            three.metric("Creators quit", f"{quit_count:,}")
            four.metric("Quit rate", f"{(quit_count / len(stability_rows) * 100) if len(stability_rows) else 0:.2f}%")

            five, six, seven, eight = st.columns(4)
            five.metric("Reached graduation", f"{reached_count:,}")
            six.metric("Graduation rate", f"{(reached_count / len(graduation_rows) * 100) if len(graduation_rows) else 0:.2f}%")
            seven.metric("Premium Invite Graduates", f"{extra_completed:,} / {len(reward_rows):,}")
            eight.metric("Creators with Extra Reward", f"{len(reward_rows):,}")

            nine, ten, eleven, twelve = st.columns(4)
            nine.metric("Active stability creators", f"{int(live_days.gt(0).sum()):,}")
            ten.metric("Valid Go LIVE days", f"{int(live_days.sum()):,}")
            eleven.metric("Valid LIVE hours", f"{total_live_hours:,.1f}")
            twelve.metric("Creator Stability pages read", f"{stability_pages:,}")

            st.caption(f"Verified source read: {stability_pages:,} Creator Stability pages, {graduation_pages:,} Creator Graduation evaluated pages, and {reward_pages:,} Extra Reward pages. Every captured creator is listed below.")

            st.subheader("Business Essentials details")
            preferred_sections = [
                "Creator Stability — Evaluated Creators",
                "Creator Graduation — 172 Evaluated",
                "Creator Graduation — 21 Reached graduation",
                "Creator Graduation — Creators with Extra Reward",
            ]
            section_names = [name for name in preferred_sections if name in set(section_series)]
            section_names.extend(name for name in section_series.dropna().unique() if name not in section_names)
            for section_name in section_names:
                section_rows = visible_business[section_series == section_name].copy()
                with st.container(border=True):
                    st.markdown(f"### {section_name}")
                    render_read_table(section_rows.drop(columns=["Section"], errors="ignore"))

    with maintenance_tab:
        st.subheader("Maintenance Rate")
        st.caption("Latest maintenance records from the three authorized Maintenance Rate source pages.")

        maintenance_data = pd.DataFrame()
        try:
            maintenance_payloads = pd.read_sql(text("SELECT payload FROM maintenance_rate_rows ORDER BY row_index"), get_engine())
            if not maintenance_payloads.empty:
                maintenance_data = pd.DataFrame(maintenance_payloads["payload"].tolist())
        except Exception:
            maintenance_data = pd.DataFrame()

        if not maintenance_data.empty and "maintained_tier" in maintenance_data.columns:
            total_maintenance = len(maintenance_data)
            maintaining_count = int(maintenance_data["maintained_tier"].fillna(False).astype(bool).sum())
            maintenance_rate = (maintaining_count / total_maintenance * 100) if total_maintenance else 0.0
            card_one, card_two, card_three = st.columns(3)
            card_one.metric("Creators in Maintenance Rate", f"{total_maintenance:,}")
            card_two.metric("Maintaining or ranked up", f"{maintaining_count:,}")
            card_three.metric("Maintenance rate", f"{maintenance_rate:.2f}%")
            st.caption("Cleaned Maintenance Rate read from the latest source pages.")

            clean_rows = []
            for _, source_row in maintenance_data.iterrows():
                raw = str(source_row.get("raw_row", "")).replace("\u0014", " ").replace("\n", " ")
                raw = re.sub(r"\s+", " ", raw).strip()
                tier_matches = re.findall(r"\bTier\s+\d+\b", raw, flags=re.IGNORECASE)
                tier_matches = ["Tier " + re.search(r"\d+", item).group() for item in tier_matches]

                progress_match = re.search(r"([\d,]+)\s*/\s*([\d,]+)", raw)
                progress = ""
                if progress_match:
                    progress = f"{progress_match.group(1).replace(',', '')}/{progress_match.group(2).replace(',', '')}"

                current_tier = tier_matches[0] if tier_matches else ""
                last_month_tier = tier_matches[-1] if len(tier_matches) > 1 else ""
                next_tier = next((tier for tier in tier_matches[1:] if tier != current_tier and tier != last_month_tier), "")
                if not next_tier and len(tier_matches) >= 3:
                    next_tier = tier_matches[2]

                valid_days_match = re.search(r"\b\d+\s*d\b", raw, flags=re.IGNORECASE)
                valid_days = valid_days_match.group(0).replace(" ", "") if valid_days_match else ""
                status = "Ranked up" if re.search(r"Ranked up", raw, flags=re.IGNORECASE) else ("Maintained tier" if re.search(r"Maintained tier", raw, flags=re.IGNORECASE) else "Not maintained")

                clean_rows.append({
                    "Creator": source_row.get("creator", ""),
                    "Tier progress": progress,
                    "Current tier": current_tier,
                    "Valid Go LIVE days": valid_days,
                    "Tier last month": last_month_tier,
                    "Maintained": "Yes" if bool(source_row.get("maintained_tier", False)) else "No",
                    "Status": status,
                })

            render_read_table(pd.DataFrame(clean_rows), height=720)
        else:
            st.info("Maintenance source pages have not supplied a complete read yet. The dashboard remains online, and these boxes will populate automatically as soon as the scheduled maintenance reader imports its next complete run.")


    with scouting_tab:
        st.subheader("Scouting")
        st.caption("Two separate reads with dedicated Agency and manager views. Refreshes run at :20 and :50 after each hour.")
        st.markdown('<div class="gh-scout-hero"><p class="gh-scout-hero-title">Creator Scouting Center</p><p class="gh-scout-hero-copy">Review applied creators and invitations by Agency or manager, with the live activity that matters for each lead.</p></div>', unsafe_allow_html=True)
        scouting = load_scouting_records()
        if scouting.empty:
            st.info("The Scouting reader is being connected. This page will show the selected Backstage reads as soon as its first scheduled capture completes.")
        else:
            scouting["assigned_manager"] = scouting["assigned_manager"].fillna("Unassigned").astype(str).str.strip().replace("", "Unassigned")
            scouting["source"] = scouting["source"].fillna("").astype(str)
            scouting["_event_at"] = pd.to_datetime(scouting["captured_at"], errors="coerce", utc=True)
            scouting_now = pd.Timestamp.now(tz="UTC")
            applied_recent = (scouting["source"] == "scouting_applied") & (scouting["_event_at"] >= scouting_now - pd.Timedelta(days=5))
            invited_recent = (scouting["source"] == "scouting_invited") & (scouting["_event_at"] >= scouting_now - pd.Timedelta(days=5))
            invited_ten_days = (scouting["source"] == "scouting_invited") & (scouting["_event_at"] >= scouting_now - pd.Timedelta(days=10))
            accepted_recent = invited_ten_days & scouting["scouting_status"].fillna("").astype(str).str.contains("accepted", case=False, na=False)
            not_accepted_recent = invited_ten_days & ~scouting["scouting_status"].fillna("").astype(str).str.contains("accepted", case=False, na=False)
            applied_box, invited_box, accepted_box, pending_box = st.columns(4)
            applied_box.metric("Applied — last 5 days", f"{int(applied_recent.sum()):,}")
            invited_box.metric("Invited — last 5 days", f"{int(invited_recent.sum()):,}")
            accepted_box.metric("Accepted — last 10 days", f"{int(accepted_recent.sum()):,}")
            pending_box.metric("Not accepted — last 10 days", f"{int(not_accepted_recent.sum()):,}")

            def render_scouting_source(source_key, heading):
                source_rows = scouting[scouting["source"] == source_key].copy()
                if source_rows.empty:
                    st.info(f"{heading} has not completed its first verified read yet.")
                    return
                manager_choices = sorted(name for name in source_rows["assigned_manager"].unique() if name)
                scouting_view = st.selectbox("Scouting page", ["Agency overview", *manager_choices], key=f"scouting_manager_{source_key}")
                view_rows = source_rows if scouting_view == "Agency overview" else source_rows[source_rows["assigned_manager"] == scouting_view].copy()
                scouting_photo_keys = {
                    "bluecollarsquad00@gmail.com": "chersade",
                    "ladykmo@outlook.com": "ladykmo",
                    "leslieclark615@yahoo.com": "leslieclark",
                    "jamiegoodin1967@protonmail.com": "pap",
                    "amazinggraceof3@gmail.com": "amazinggrace",
                    "hello@blacksheepcreations.com": "joedickerson",
                    "steenieg33@gmail.com": "glittersunfun",
                    "tiktoktonip@gmail.com": "tonipeters",
                    "keeley.ehrenreich@gmail.com": "lacie",
                    "ariana.segal13@gmail.com": "ariana",
                }
                selected_photo_key = "agency" if scouting_view == "Agency overview" else scouting_photo_keys.get(scouting_view.casefold(), "".join(char for char in scouting_view.casefold() if char.isalnum()))
                selected_photo_name = manager_logo_files.get(selected_photo_key)
                selected_photo_path = (Path(__file__).resolve().parent / "assets" / "agency-logo.png") if selected_photo_key == "agency" else (Path(__file__).resolve().parent / "assets" / "manager-logos" / selected_photo_name if selected_photo_name else None)
                st.markdown(f'<div class="gh-scout-manager-label">{heading} • {scouting_view}</div>', unsafe_allow_html=True)
                if selected_photo_path and selected_photo_path.exists():
                    photo_left, photo_center, photo_right = st.columns([1, 1, 1])
                    with photo_center:
                        st.image(str(selected_photo_path), width=180)
                total_col, live_col, diamond_col, manager_col = st.columns(4)
                total_col.metric("Creators", f"{len(view_rows):,}")
                live_col.metric("LIVE streams", f'{int(pd.to_numeric(view_rows["live_streams"], errors="coerce").fillna(0).sum()):,}')
                diamond_col.metric("Diamonds", f'{int(pd.to_numeric(view_rows["diamonds"], errors="coerce").fillna(0).sum()):,}')
                manager_col.metric("Managers", f'{view_rows["assigned_manager"].nunique():,}')
                if scouting_view == "Agency overview":
                    st.markdown("#### Manager pages")
                    manager_summary = (view_rows.groupby("assigned_manager", dropna=False)
                        .agg(Creators=("username", "count"), LIVE_streams=("live_streams", "sum"), Diamonds=("diamonds", "sum"))
                        .reset_index().rename(columns={"assigned_manager": "Manager", "LIVE_streams": "LIVE streams"})
                        .sort_values(["Creators", "Manager"], ascending=[False, True]))
                    for manager_start in range(0, len(manager_summary), 3):
                        manager_boxes = st.columns(3)
                        for manager_box, (_, manager_row) in zip(manager_boxes, manager_summary.iloc[manager_start:manager_start + 3].iterrows()):
                            with manager_box:
                                st.metric(str(manager_row["Manager"]), f'{int(manager_row["Creators"]):,} creators')
                                st.caption(f'{int(manager_row["LIVE streams"]):,} LIVE streams • {int(manager_row["Diamonds"]):,} diamonds')
                st.markdown(f"#### {scouting_view}")
                search_scout = st.text_input("Search creators", key=f"scouting_search_{source_key}")
                if search_scout:
                    view_rows = view_rows[view_rows["username"].fillna("").astype(str).str.contains(search_scout, case=False, na=False)]
                display_columns = ["username", "followers", "likes", "applied_to_join", "scouting_status", "live_streams", "diamonds", "live_hours", "avg_live_viewers", 
                "assigned_manager", "source_label", "lead_expiry", "captured_at"]
                labels = {"username":"Creator", "followers":"Followers", "likes":"Likes", "applied_to_join":"Applied", "scouting_status":"Scouting status", "live_streams":"LIVE streams", "diamonds":"Diamonds", "live_hours":"LIVE hours", "avg_live_viewers":"Avg. LIVE viewers", "invitation_type":"Invitation type", "assigned_manager":"Manager", "source_label":"Source", "lead_expiry":"Lead expires", "captured_at":"Last refreshed"}
                display_frame = view_rows[[column for column in display_columns if column in view_rows.columns]].rename(columns=labels)
                if all(column in display_frame.columns for column in ["Creator", "Followers", "Likes"]):
                    follower_values = pd.to_numeric(display_frame["Followers"], errors="coerce").fillna(0)
                    like_values = pd.to_numeric(display_frame["Likes"], errors="coerce").fillna(0)
                    display_frame["Creator"] = [
                        f"{name}\n{followers:,.0f} followers\n{likes:,.0f} likes"
                        for name, followers, likes in zip(display_frame["Creator"], follower_values, like_values)
                    ]
                    display_frame = display_frame.drop(columns=["Followers", "Likes"])
                if source_key == "scouting_applied":
                    application_types = view_rows.loc[display_frame.index, "invitation_type"].fillna("").astype(str)
                    stream_values = pd.to_numeric(view_rows.loc[display_frame.index, "live_streams"], errors="coerce").fillna(0)
                    diamond_values = pd.to_numeric(view_rows.loc[display_frame.index, "diamonds"], errors="coerce").fillna(0)
                    hour_values = pd.to_numeric(view_rows.loc[display_frame.index, "live_hours"], errors="coerce").fillna(0)
                    viewer_values = pd.to_numeric(view_rows.loc[display_frame.index, "avg_live_viewers"], errors="coerce").fillna(0)
                    display_frame = pd.DataFrame({
                        "Creator": display_frame["Creator"],
                        "Applied": ["Yes\n" + (value.strip() or "Not available") for value in application_types],
                        "Last 30 days": [f"{streams:,.0f} LIVE streams • {hours:g} h\n{diamonds:,.0f} Diamonds • {viewers:,.0f} Avg. LIVE viewers" for streams, hours, diamonds, viewers in zip(stream_values, hour_values, diamond_values, viewer_values)],
                        "Assigned to": view_rows.loc[display_frame.index, "assigned_manager"].fillna("Unassigned").astype(str),
                    }, index=display_frame.index)
                else:
                    display_frame = pd.DataFrame({
                        "Creator": display_frame["Creator"],
                        "Scouting status": view_rows.loc[display_frame.index, "scouting_status"].fillna("").astype(str),
                        "Invitation type": view_rows.loc[display_frame.index, "invitation_type"].fillna("").astype(str),
                        "Assigned to": view_rows.loc[display_frame.index, "assigned_manager"].fillna("Unassigned").astype(str),
                        "Expires": view_rows.loc[display_frame.index, "lead_expiry"].fillna("No expiry").astype(str),
                    }, index=display_frame.index)
                st.markdown("<div class='gh-scout-table'>" + display_frame.to_html(index=False, escape=True) + "</div>", unsafe_allow_html=True)

            applied_scouting_tab, invitation_scouting_tab = st.tabs(["⚡ Applied — Quick Response", "Invitations"])
            with applied_scouting_tab:
                render_scouting_source("scouting_applied", "Applied")
            with invitation_scouting_tab:
                render_scouting_source("scouting_invited", "Invitations")

    with tier_guide_tab:
        st.subheader("Tier & Level Guide")
        st.caption("Monthly requirements for creator tiers and LIVE activeness levels.")
        tier_col, level_col = st.columns(2)
        with tier_col:
            st.markdown("#### Diamond path")
            st.dataframe(pd.DataFrame([
                {"Tier": "Tier 1", "Monthly diamonds": "0"},
                {"Tier": "Tier 2", "Monthly diamonds": "100K"},
                {"Tier": "Tier 3", "Monthly diamonds": "200K"},
                {"Tier": "Tier 4", "Monthly diamonds": "300K"},
                {"Tier": "Tier 5", "Monthly diamonds": "500K"},
                {"Tier": "Tier 6", "Monthly diamonds": "1M"},
                {"Tier": "Tier 7", "Monthly diamonds": "1.6M"},
                {"Tier": "Tier 8", "Monthly diamonds": "3M"},
                {"Tier": "Tier 9", "Monthly diamonds": "5M"},
                {"Tier": "Tier 10", "Monthly diamonds": "8M"},
            ]), use_container_width=True, hide_index=True, height=430)
        with level_col:
            st.markdown("#### Time (Go LIVE) path")
            st.dataframe(pd.DataFrame([
                {"Level": "Level 1", "Monthly diamonds": "100", "LIVE duration": "20 hours", "Valid LIVE days": "8"},
                {"Level": "Level 2", "Monthly diamonds": "100", "LIVE duration": "30 hours", "Valid LIVE days": "11"},
                {"Level": "Level 3", "Monthly diamonds": "100", "LIVE duration": "40 hours", "Valid LIVE days": "15"},
                {"Level": "Level 4", "Monthly diamonds": "100", "LIVE duration": "60 hours", "Valid LIVE days": "18"},
                {"Level": "Level 5", "Monthly diamonds": "100", "LIVE duration": "80 hours", "Valid LIVE days": "22"},
            ]), use_container_width=True, hide_index=True, height=245)


    with access_tab:
        st.subheader("Access Management")
        st.caption("Approved Google accounts. Owners and administrators can add, restore, or deactivate access here.")

        signed_in_email = google_signed_in_email()
        access_view = access_people.copy()
        if not access_view.empty:
            access_view["email"] = access_view["email"].fillna("").astype(str).str.casefold()
            access_view["role"] = access_view["role"].fillna("member").astype(str).str.casefold()
            access_view["active"] = access_view["active"].fillna(False).astype(bool)
        try:
            live_google_access = google_iap_access_members()
            iap_access_error = ""
        except Exception as error:
            live_google_access = set()
            iap_access_error = str(error)
        if live_google_access:
            known_emails = set(access_view["email"]) if not access_view.empty else set()
            missing_emails = sorted(live_google_access - known_emails)
            if missing_emails:
                access_view = pd.concat([access_view, pd.DataFrame([{"email": email, "role": "member", "active": True, "added_at": "", "updated_at": ""} for email in missing_emails])], ignore_index=True)
            access_view.loc[access_view["email"].isin(live_google_access), "active"] = True
        actor_row = access_view[(access_view["email"] == signed_in_email) & access_view["active"]] if signed_in_email and not access_view.empty else pd.DataFrame()
        actor_role = str(actor_row.iloc[0]["role"]) if not actor_row.empty else ""
        can_manage_access = actor_role in {"owner", "admin"}

        if not signed_in_email:
            st.warning("Google sign-in details are not available to this dashboard session yet. Access changes stay locked until Google IAP forwards the signed-in email.")
        elif not can_manage_access:
            st.error("Only dashboard owners and administrators can manage access.")
        else:
            st.success(f"Signed in as {signed_in_email} ({actor_role.title()}).")
            with st.form("add_dashboard_access", clear_on_submit=True):
                add_email = st.text_input("Google email address", placeholder="name@gmail.com")
                available_roles = ["member", "admin"] + (["owner"] if actor_role == "owner" else [])
                add_role = st.selectbox("Role", available_roles, format_func=lambda role: {"member": "Member — view dashboard", "admin": "Administrator — manage access", "owner": "Owner — full control"}[role])
                add_submit = st.form_submit_button("Add or restore access", type="primary")
            if add_submit:
                try:
                    set_google_iap_access(add_email, True)
                    save_access_person(add_email, add_role)
                    st.success(f"Access saved for {add_email.strip().casefold()}.")
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))
                except Exception:
                    st.error("The access list could not be updated. Please try again.")

            st.subheader("Current access")
            st.caption("Change a role, remove access, or restore a previously removed account. Changes are saved immediately.")
            active_people = access_view[access_view["active"]].copy() if not access_view.empty else pd.DataFrame()
            role_labels = {"member": "User — view dashboard", "admin": "Administrator — manage access", "owner": "Owner — full control"}
            manageable_roles = ["member", "admin"] + (["owner"] if actor_role == "owner" else [])
            if active_people.empty:
                st.info("No active approved accounts are listed yet.")
            else:
                for _, person in active_people.sort_values(["role", "email"]).iterrows():
                    person_email = str(person["email"])
                    person_role = str(person["role"]).casefold()
                    left, middle, save_col, remove_col = st.columns([4, 3, 1.2, 1.2])
                    left.write(person_email)
                    is_self = person_email == signed_in_email
                    if is_self:
                        middle.write("Owner — current account")
                        save_col.caption("Protected")
                        remove_col.caption("Current account")
                    else:
                        selected_role = middle.selectbox(
                            "Role",
                            manageable_roles,
                            index=manageable_roles.index(person_role) if person_role in manageable_roles else 0,
                            format_func=lambda role: role_labels[role],
                            key=f"access_role_{person_email}",
                            label_visibility="collapsed",
                        )
                        if save_col.button("Save", key=f"save_access_{person_email}"):
                            try:
                                save_access_person(person_email, selected_role)
                                st.success(f"Updated {person_email}.")
                                st.rerun()
                            except Exception:
                                st.error("That account could not be updated. Please try again.")
                        if remove_col.button("Remove", key=f"remove_access_{person_email}"):
                            try:
                                set_google_iap_access(person_email, False)
                                set_google_iap_access(person_email, False)
                                deactivate_access_person(person_email)
                                st.rerun()
                            except Exception:
                                st.error("That account could not be removed. Please try again.")

            inactive_people = access_view[~access_view["active"]].copy() if not access_view.empty else pd.DataFrame()
            if not inactive_people.empty:
                with st.expander("Removed accounts"):
                    st.caption("Restore an account when it should have dashboard access again.")
                    for _, person in inactive_people.sort_values(["role", "email"]).iterrows():
                        removed_email = str(person["email"])
                        restore_left, restore_role, restore_action = st.columns([4, 3, 1.5])
                        restore_left.write(removed_email)
                        restore_choice = restore_role.selectbox(
                            "Role", manageable_roles,
                            index=manageable_roles.index(str(person["role"]).casefold()) if str(person["role"]).casefold() in manageable_roles else 0,
                            format_func=lambda role: role_labels[role],
                            key=f"restore_role_{removed_email}", label_visibility="collapsed",
                        )
                        if restore_action.button("Restore", key=f"restore_access_{removed_email}"):
                            try:
                                set_google_iap_access(removed_email, True)
                                set_google_iap_access(removed_email, True)
                                save_access_person(removed_email, restore_choice)
                                st.success(f"Restored {removed_email}.")
                                st.rerun()
                            except Exception:
                                st.error("That account could not be restored. Please try again.")


if __name__ == "__main__":
    main()
