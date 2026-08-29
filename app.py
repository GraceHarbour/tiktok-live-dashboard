import base64
import io
import json
import os
import re
















import pandas as pd
import plotly.express as px
import streamlit as st
import yaml
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
































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
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=2,
        pool_timeout=10,
        pool_recycle=120,
        connect_args={"connect_timeout": 10},
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
        return pd.read_sql(text("SELECT email, role, added_at FROM dashboard_access_people ORDER BY role, email"), connection)
















@st.cache_data(ttl=300)
def load_monthly_metrics():
    with get_engine().connect() as connection:
        return pd.read_sql(text("SELECT metric_name, metric_value, updated_at FROM dashboard_monthly_metrics ORDER BY metric_name"), connection)

















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








def main():
    st.markdown(
        """
        <style>
        :root { --gh-navy: #030817; --gh-deep: #071a3a; --gh-blue: #102d6b; --gh-gold: #f5c542; --gh-violet: #8b5cf6; --gh-text: #eef4ff; }
        .stApp { position: relative; overflow-x: hidden; background: radial-gradient(circle at 86% 2%, rgba(111,69,202,.27), transparent 24rem), radial-gradient(circle at 18% 0%, rgba(19,80,184,.24), transparent 28rem), linear-gradient(150deg, var(--gh-navy), #06142f 48%, #02050e); color: var(--gh-text); }
        .stApp::before { content: "⚓"; position: fixed; z-index: 0; right: -3rem; top: 4rem; color: rgba(245,197,66,.010); font-size: 28rem; line-height: 1; transform: rotate(-11deg); pointer-events: none; }
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
[data-testid="stTabs"] [data-baseweb="tab"] { color: #fff7c4 !important; font-size: 1.12rem !important; font-weight: 900 !important; letter-spacing: .025em; padding: .7rem 1.05rem !important; text-shadow: 0 1px 8px #000; }
[data-testid="stTabs"] [aria-selected="true"] { color: #ffd95a !important; border-bottom-color: #ffd95a !important; }
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








    manager_tab, goals_tab, prior_month_tab, business_tab, scouting_tab, access_tab = st.tabs([
        "Dashboard",
        "Goal Management",
        "Goal Management Prior Month",
        "Business Essentials",
        "Scouting",
        "Access & Data",
    ])








    with manager_tab:
        pass








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
            not_maintained_text = tier_text.str.contains("not maintained") | rank_text.str.contains("not maintained")
            
            ranked_mask = tier_text.str.contains("rank") | rank_text.str.contains("rank")
            maintained_mask = ~ranked_mask & ~not_maintained_text & (
                tier_text.str.contains("maintain") | rank_text.str.contains("maintain")
            )
            not_maintained_mask = not_maintained_text | ~(ranked_mask | maintained_mask)
            above_200k = int((visible_diamonds >= 200000).sum())
            selected_manager_rows = managers if choice == "All managers" else managers[managers["_manager"] == choice]
            new_creators = int(numeric_series(selected_manager_rows, "new_creators").sum()) if not selected_manager_rows.empty else 0

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
                        st.dataframe(
                            creator_goal_display(frame, include_manager=include_manager),
                            use_container_width=True,
                            hide_index=True,
                        )

            selection_label = "all managers" if choice == "All managers" else choice
            st.caption(f"Showing every current Goal Management field for {selection_label}.")
            goal_section(
                "All creator goals",
                visible,
                "No creator-goal records match this selection.",
                include_manager=choice == "All managers",
            )
            goal_section(
                "Maintaining tier",
                visible[maintained_mask].copy(),
                "No creators are currently marked as maintaining tier.",
                include_manager=choice == "All managers",
            )
            goal_section(
                "Ranking up",
                visible[ranked_mask].copy(),
                "No creators are currently marked as ranking up.",
                include_manager=choice == "All managers",
            )
            goal_section(
                "Tier not maintained",
                visible[not_maintained_mask].copy(),
                "No creators are currently marked as not maintained.",
                include_manager=choice == "All managers",
            )


    with prior_month_tab:
        shared_prior = load_shared_prior_month()
        st.caption("A shared prior-month view for all dashboard visitors.")
        if shared_prior:
            shared_frame = pd.DataFrame(shared_prior["rows"])
            shared_columns = [column for column in shared_prior["columns"] if column in shared_frame.columns]
            st.subheader("Current shared prior-month view")
            st.caption(f"Source: {shared_prior['file_name']} • last published {shared_prior['uploaded_at']}")
            if shared_columns:
                st.dataframe(shared_frame[shared_columns], use_container_width=True, hide_index=True)
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
                if selected_columns:
                    st.dataframe(prior_data[selected_columns], use_container_width=True, hide_index=True)
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
                    st.dataframe(section_rows.drop(columns=["Section"], errors="ignore"), use_container_width=True, hide_index=True)

    with scouting_tab:
        st.caption("Scouting records will appear here when the scouting capture is imported.")
        st.info("No scouting records have been imported yet.")








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
