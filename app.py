import io
import hmac
import os
import re

import pandas as pd
import plotly.express as px
import streamlit as st
import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine, text


load_dotenv()
st.set_page_config(page_title="TikTok LIVE Creator Dashboard", page_icon="🎙️", layout="wide")


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


def :
    viewer_password = secret_value("VIEWER_PASSWORD")
    if not viewer_password:
        st.error("This dashboard is locked because VIEWER_PASSWORD has not been configured.")
        st.info("The owner must add VIEWER_PASSWORD in Streamlit Secrets before the dashboard can be viewed.")
        st.stop()

    if st.session_state.get("viewer_authenticated"):
        with st.sidebar:
            if st.button("Sign out", use_container_width=True):
                st.session_state["viewer_authenticated"] = False
                st.rerun()
        return

    st.title("TikTok LIVE Creator Dashboard")
    st.write("Enter the private viewing password to open the dashboard.")
    entered_password = st.text_input("Viewing password", type="password")
    if st.button("Open dashboard", type="primary"):
        if hmac.compare_digest(entered_password, viewer_password):
            st.session_state["viewer_authenticated"] = True
            st.rerun()
        else:
            st.error("The viewing password is not correct.")
    st.stop()


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


@st.cache_data(ttl=300)
def load_latest_update():
    with get_engine().connect() as connection:
        return pd.read_sql(text("SELECT * FROM data_updates ORDER BY updated_at DESC LIMIT 1"), connection)


def live_duration_hours(value):
    if pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text_value = str(value)
    hours = re.search(r"([\d.]+)h", text_value)
    minutes = re.search(r"([\d.]+)m", text_value)
    seconds = re.search(r"([\d.]+)s", text_value)
    return (
        (float(hours.group(1)) if hours else 0)
        + (float(minutes.group(1)) / 60 if minutes else 0)
        + (float(seconds.group(1)) / 3600 if seconds else 0)
    )


def parse_backstage_export(uploaded_file):
    filename = uploaded_file.name.lower()
    if filename.endswith(".csv"):
        source = pd.read_csv(uploaded_file, dtype=str)
    else:
        source = pd.read_excel(uploaded_file, dtype=str)

    required = ["Creator ID", "Creator's username", "Creator Network manager", "Diamonds", "LIVE duration", "Valid go LIVE days", "Tier status"]
    missing = [column for column in required if column not in source.columns]
    if missing:
        raise ValueError("This is not a supported Backstage Creator data export. Missing: " + ", ".join(missing))

    existing_manager_names = {}
    existing_manager_goals = {}
    try:
        current_managers = load_goal_managers()
        existing_manager_names = current_managers.set_index("manager")["manager_name"].to_dict()
        existing_manager_goals = current_managers.set_index("manager").to_dict("index")
    except Exception:
        pass

    creator_records = []
    for _, row in source.iterrows():
        username = "" if pd.isna(row["Creator's username"]) else str(row["Creator's username"]).strip()
        raw_id = "" if pd.isna(row["Creator ID"]) else str(row["Creator ID"]).split(".", 1)[0].strip()
        numeric_creator_id = bool(re.fullmatch(r"\d{10,}", raw_id))
        if not username and numeric_creator_id:
            username = raw_id
        if not username:
            continue
        creator_id = raw_id if numeric_creator_id else f"missing:{username}"
        manager = "Unassigned" if pd.isna(row["Creator Network manager"]) else str(row["Creator Network manager"]).strip()
        if not manager or manager == "-":
            manager = "Unassigned"
        manager_name = existing_manager_names.get(manager, manager.split("@", 1)[0] if manager != "Unassigned" else "Unassigned")
        group_name = "" if "Group" not in source.columns or pd.isna(row.get("Group")) else str(row.get("Group")).strip()
        creator_records.append(
            {
                "creator_id": creator_id,
                "username": username,
                "manager": manager,
                "manager_name": manager_name,
                "group_name": group_name,
                "diamonds": int(pd.to_numeric(row["Diamonds"], errors="coerce") if pd.notna(pd.to_numeric(row["Diamonds"], errors="coerce")) else 0),
                "valid_live_days": int(pd.to_numeric(row["Valid go LIVE days"], errors="coerce") if pd.notna(pd.to_numeric(row["Valid go LIVE days"], errors="coerce")) else 0),
                "valid_live_hours": live_duration_hours(row["LIVE duration"]),
                "estimated_bonus": 0.0,
                "tier_status": "" if pd.isna(row["Tier status"]) else str(row["Tier status"]).strip(),
                "rank_up_progress": "",
                "activeness_level": 0,
                "live_now": 0,
            }
        )

    creators_frame = pd.DataFrame(creator_records)
    if creators_frame.empty:
        raise ValueError("The Backstage export did not contain any usable creator records.")
    creators_frame = creators_frame.drop_duplicates(subset=["creator_id"], keep="last")
    manager_records = []
    assigned = creators_frame[creators_frame["manager"] != "Unassigned"]
    for manager, group in assigned.groupby("manager"):
        previous = existing_manager_goals.get(manager, {})
        manager_records.append(
            {
                "manager": manager,
                "manager_name": previous.get("manager_name") or group["manager_name"].iloc[0],
                "role": previous.get("role", "Manager"),
                "group_name": previous.get("group_name") or next((value for value in group["group_name"] if value), ""),
                "diamonds": int(group["diamonds"].sum()),
                "diamond_goal": int(previous.get("diamond_goal", 0) or 0),
                "new_creators": int(previous.get("new_creators", 0) or 0),
                "new_creator_goal": int(previous.get("new_creator_goal", 0) or 0),
                "managed_creators": int(len(group)),
            }
        )
    return creators_frame, pd.DataFrame(manager_records)


def replace_goal_data(creators_frame, managers_frame, source_file):
    today = pd.Timestamp.now(tz="UTC")
    period_start = today.replace(day=1).date().isoformat()
    period_end = today.date().isoformat()
    performance_records = []
    for manager, group in creators_frame[creators_frame["manager"] != "Unassigned"].groupby("manager"):
        active_creators = len(group)
        performance_records.append(
            {
                "manager": manager,
                "active_creators": active_creators,
                "live_streams": 0,
                "valid_live_creators": int((group["valid_live_days"] > 0).sum()),
                "live_hours": float(group["valid_live_hours"].sum()),
                "creators_under_15h_pct": float((group["valid_live_hours"] < 15).mean() * 100) if active_creators else 0,
                "diamonds": int(group["diamonds"].sum()),
                "diamond_goal": int(managers_frame.loc[managers_frame["manager"] == manager, "diamond_goal"].iloc[0]),
                "diamond_change_pct": 0.0,
                "period_start": period_start,
                "period_end": period_end,
            }
        )
    with get_engine().begin() as connection:
        connection.execute(text("DELETE FROM goal_creators"))
        connection.execute(text("DELETE FROM goal_managers"))
        connection.execute(text("DELETE FROM manager_performance"))
        if not creators_frame.empty:
            connection.execute(
                text("INSERT INTO goal_creators VALUES (:creator_id,:username,:manager,:manager_name,:group_name,:diamonds,:valid_live_days,:valid_live_hours,:estimated_bonus,:tier_status,:rank_up_progress,:activeness_level,:live_now)"),
                creators_frame.to_dict("records"),
            )
        if not managers_frame.empty:
            connection.execute(
                text("INSERT INTO goal_managers VALUES (:manager,:manager_name,:role,:group_name,:diamonds,:diamond_goal,:new_creators,:new_creator_goal,:managed_creators)"),
                managers_frame.to_dict("records"),
            )
        if performance_records:
            connection.execute(
                text("INSERT INTO manager_performance (manager,active_creators,live_streams,valid_live_creators,live_hours,creators_under_15h_pct,diamonds,diamond_goal,diamond_change_pct,period_start,period_end) VALUES (:manager,:active_creators,:live_streams,:valid_live_creators,:live_hours,:creators_under_15h_pct,:diamonds,:diamond_goal,:diamond_change_pct,:period_start,:period_end)"),
                performance_records,
            )
        connection.execute(
            text("INSERT INTO data_updates VALUES (:updated_at,:source_file,:creator_rows)"),
            {"updated_at": pd.Timestamp.now(tz="UTC").isoformat(), "source_file": source_file, "creator_rows": int(len(creators_frame))},
        )




st.title("TikTok LIVE Creator Dashboard")
st.caption("Manager performance, Goal management creators, and creator roster • Backstage snapshot")

try:
    ensure_schema()
    creators = load_creators()
    manager_performance = load_manager_performance()
    goal_managers = load_goal_managers()
    goal_creators = load_goal_creators()
    latest_update = load_latest_update()
except Exception as exc:
    st.error("The dashboard could not load creator data.")
    st.code(str(exc))
    st.info("Check DATABASE_URL and the table/column mapping in config.yaml.")
    st.stop()

for field in ["creator_name", "username", "status", "agency", "manager", "country"]:
    if field not in creators:
        creators[field] = ""
    creators[field] = creators[field].fillna("").astype(str)

if "joined_at" in creators:
    creators["joined_at"] = pd.to_datetime(creators["joined_at"], errors="coerce")

with st.sidebar:
    st.header("Filters")
    search = st.text_input("Creator or username")
    statuses = st.multiselect("Account status", sorted(x for x in creators["status"].unique() if x))
    agencies = st.multiselect("Agency/team", sorted(x for x in creators["agency"].unique() if x))
    manager_options = sorted(
        set(x for x in creators["manager"].unique() if x and x != "Unassigned")
        | set(manager_performance["manager"].dropna())
        | set(goal_managers["manager"].dropna())
    )
    managers = st.multiselect("Manager", manager_options)
    countries = st.multiselect("Country", sorted(x for x in creators["country"].unique() if x))
    hour_coverage_target = st.slider("Hours target: creators at 15+ hours", 0, 100, 50, 5, format="%d%%")
    if st.button("Refresh data", use_container_width=True):
        load_creators.clear()
        load_manager_performance.clear()
        load_goal_managers.clear()
        load_goal_creators.clear()
        load_latest_update.clear()
        st.rerun()

filtered = creators.copy()
if search:
    mask = filtered["creator_name"].str.contains(search, case=False, na=False) | filtered["username"].str.contains(search, case=False, na=False)
    filtered = filtered[mask]
if statuses:
    filtered = filtered[filtered["status"].isin(statuses)]
if agencies:
    filtered = filtered[filtered["agency"].isin(agencies)]
if managers:
    filtered = filtered[filtered["manager"].isin(managers)]
if countries:
    filtered = filtered[filtered["country"].isin(countries)]

performance = manager_performance.copy()
if managers:
    performance = performance[performance["manager"].isin(managers)]
performance["manager_label"] = performance["manager"].str.split("@").str[0]
performance["valid_live_rate"] = (performance["valid_live_creators"] / performance["active_creators"].replace(0, pd.NA) * 100).fillna(0)
performance["creators_15h_plus_pct"] = 100 - performance["creators_under_15h_pct"]
performance["hours_maintained"] = performance["creators_15h_plus_pct"] >= hour_coverage_target
performance["diamond_goal_progress"] = (performance["diamonds"] / perforance["diamond_goal"].replace(0, pd.NA) * 100).fillna(0)
performance["diamonds_maintained"] = performance["diamond_goal_progress"] >= 100
performance["hours_status"] = performance["hours_maintained"].map({True: "Maintained", False: "Needs attention"})

goal_manager_view = goal_managers.copy()
goal_creator_view = goal_creators.copy()
if managers:
    goal_manager_view = goal_manager_view[goal_manager_view["manager"].isin(managers)]
    goal_creator_view = goal_creator_view[goal_creator_view["manager"].isin(managers)]
if search:
    goal_creator_view = goal_creator_view[
        goal_creator_view["username"].str.contains(search, case=False, na=False)
        | goal_creator_view["creator_id"].str.contains(search, case=False, na=False)
    ]

performance_tab, goal_tab, admin_tab, roster_tab = st.tabs(["Manager performance", "Goal managers & creators", "Update data", "Application roster"])

with performance_tab:
    if performance.empty:
        st.info("No manager performance matches the current filter.")
    else:
        period_start = pd.to_datetime(performance["period_start"].iloc[0]).strftime("%b %d")
        period_end = pd.to_datetime(performance["period_end"].iloc[0]).strftime("%b %d, %Y")
        st.caption(f"Backstage Group Data and Administration → Goal management • {period_start}–{period_end} (UTC) • updated daily")

        p1, p2, p3, p4, p5 = st.columns(5)
        p1.metric("Managers", f"{len(performance):,}")
        p2.metric("Active creators", f"{performance['active_creators'].sum():,}")
        p3.metric("Valid LIVE creators", f"{performance['valid_live_creators'].sum():,}")
        p4.metric("LIVE hours", f"{performance['live_hours'].sum():,.0f}")
        p5.metric("Diamonds", f"{performance['diamonds'].sum() / 1_000_000:.2f}M")

        chart_left, chart_right = st.columns(2)
        with chart_left:
            st.subheader("Diamonds by manager")
            diamond_chart = performance.sort_values("diamonds", ascending=True)
            fig_diamonds = px.bar(diamond_chart, x="diamonds", y="manager_label", orientation="h", text_auto=".3s")
            fig_diamonds.update_layout(xaxis_title="Diamonds", yaxis_title="Manager", showlegend=False, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig_diamonds, width="stretch")
        with chart_right:
            st.subheader("LIVE hours by manager")
            hours_chart = performance.sort_values("live_hours", ascending=True)
            fig_hours = px.bar(hours_chart, x="live_hours", y="manager_label", orientation="h", text_auto=".3s", color="hours_status", color_discrete_map={"Maintained": "#16a34a", "Needs attention": "#f59e0b"})
            fig_hours.update_layout(xaxis_title="LIVE hours", yaxis_title="Manager", legend_title=f"{hour_coverage_target}% at 15+ hours", margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig_hours, width="stretch")

        st.subheader("Valid LIVE coverage")
        coverage = performance[["manager_label", "active_creators", "valid_live_creators"]].melt(id_vars="manager_label", var_name="measure", value_name="creators")
        coverage["measure"] = coverage["measure"].map({"active_creators": "Active creators", "valid_live_creators": "Valid LIVE creators"})
        fig_coverage = px.bar(coverage, x="manager_label", y="creators", color="measure", barmode="group", text_auto=True)
        fig_coverage.update_layout(xaxis_title="Manager", yaxis_title="Creators", legend_title="", margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig_coverage, width="stretch")

        st.subheader("Tier-maintenance view")
        st.caption("Hours status uses the adjustable share of creators reaching 15+ LIVE hours. Diamond status compares current diamonds with the manager’s August target in Backstage Goal management. Hours status is a dashboard rule; TikTok’s individual creator tier label remains creator-level.")
        detail = performance[["manager", "active_creators", "valid_live_creators", "valid_live_rate", "live_streams", "live_hours", "creators_15h_plus_pct", "hours_maintained", "diamonds", "diamond_goal", "diamond_goal_progress", "diamond_change_pct", "diamonds_maintained"]].copy()
        detail["hours_maintained"] = detail["hours_maintained"].map({True: "Maintained", False: "Needs attention"})
        detail["diamonds_maintained"] = detail["diamonds_maintained"].map({True: "Goal met", False: "In progress"})
        detail.columns = ["Manager", "Active creators", "Valid LIVE creators", "Valid LIVE rate", "LIVE streams", "LIVE hours", "Creators at 15+ hours", "Hours status", "Diamonds", "Diamond goal", "Goal progress", "Diamond change", "Diamond status"]
        st.dataframe(
            detail.sort_values("Diamonds", ascending=False),
            width="stretch",
            hide_index=True,
            column_config={
                "Valid LIVE rate": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
                "Creators at 15+ hours": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
                "LIVE hours": st.column_config.NumberColumn(format="%.1f"),
                "Diamonds": st.column_config.NumberColumn(format="localized"),
                "Diamond goal": st.column_config.NumberColumn(format="localized"),
                "Goal progress": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
                "Diamond change": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )
        performance_csv = detail.to_csv(index=False).encode("utf-8")
        st.download_button("Download manager performance", performance_csv, "manager-performance.csv", "text/csv")

with goal_tab:
    st.caption("Administration → Goal management → View by creator • August 2026")
    manager_name_options = sorted(
        name for name in goal_creator_view["manager_name"].fillna("").astype(str).unique()
        if name and name != "Unassigned"
    )
    selected_goal_manager = st.selectbox(
        "Select a manager",
        ["All managers", *manager_name_options],
        key="goal_manager_selector",
    )
    if selected_goal_manager != "All managers":
        goal_manager_view = goal_manager_view[
            goal_manager_view["manager_name"].fillna("").astype(str) == selected_goal_manager
        ]
        goal_creator_view = goal_creator_view[
            goal_creator_view["manager_name"].fillna("").astype(str) == selected_goal_manager
        ]
    if not latest_update.empty:
        updated_at = pd.to_datetime(latest_update.iloc[0]["updated_at"], errors="coerce")
        if pd.notna(updated_at):
            st.caption(f"Latest shared update: {updated_at.strftime('%b %d, %Y %I:%M %p UTC')} • {latest_update.iloc[0]['creator_rows']:,} creators")
    if goal_manager_view.empty:
        st.info("No Goal management records match the selected manager filter.")
    else:
        goal_manager_view["diamond_progress"] = (
            goal_manager_view["diamonds"] / goal_manager_view["diamond_goal"].replace(0, pd.NA) * 100
        ).fillna(0)
        goal_manager_view["new_creator_progress"] = (
            goal_manager_view["new_creators"] / goal_manager_view["new_creator_goal"].replace(0, pd.NA) * 100
        ).fillna(0)

        g1, g2, g3, g4, g5 = st.columns(5)
        g1.metric("Listed managers", f"{len(goal_manager_view):,}")
        g2.metric("Managed creators", f"{goal_manager_view['managed_creators'].sum():,}")
        g3.metric("Creator rows", f"{len(goal_creator_view):,}")
        g4.metric("Diamonds", f"{goal_manager_view['diamonds'].sum() / 1_000_000:.2f}M")
        maintained_count = goal_creator_view["tier_status"].str.startswith(("Maintained", "Ranked up"), na=False).sum()
        g5.metric("Tier maintained/ranked up", f"{maintained_count:,}")

        goal_chart_left, goal_chart_right = st.columns(2)
        with goal_chart_left:
            st.subheader("Diamonds vs goal by manager")
            manager_goal_chart = goal_manager_view[["manager_name", "diamonds", "diamond_goal"]].melt(
                id_vars="manager_name", var_name="measure", value_name="diamonds_value"
            )
            manager_goal_chart["measure"] = manager_goal_chart["measure"].map(
                {"diamonds": "Current diamonds", "diamond_goal": "Diamond goal"}
            )
            fig_goal = px.bar(
                manager_goal_chart,
                x="diamonds_value",
                y="manager_name",
                color="measure",
                orientation="h",
                barmode="group",
            )
            fig_goal.update_layout(xaxis_title="Diamonds", yaxis_title="Manager", legend_title="", margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig_goal, width="stretch")
        with goal_chart_right:
            st.subheader("Valid LIVE hours by manager")
            creator_hours = goal_creator_view.groupby(["manager", "manager_name"], as_index=False).agg(valid_live_hours=("valid_live_hours", "sum"))
            creator_hours = creator_hours.sort_values("valid_live_hours", ascending=True)
            fig_creator_hours = px.bar(creator_hours, x="valid_live_hours", y="manager_name", orientation="h", text_auto=".3s")
            fig_creator_hours.update_layout(xaxis_title="Valid LIVE hours", yaxis_title="Manager", showlegend=False, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig_creator_hours, width="stretch")

        st.subheader("Valid LIVE days by manager")
        creator_days = goal_creator_view.groupby(["manager", "manager_name"], as_index=False).agg(
            valid_live_days=("valid_live_days", "sum")
        ).sort_values("valid_live_days", ascending=True)
        fig_creator_days = px.bar(
            creator_days,
            x="valid_live_days",
            y="manager_name",
            orientation="h",
            text_auto=True,
        )
        fig_creator_days.update_layout(
            xaxis_title="Total valid LIVE days",
            yaxis_title="Manager",
            showlegend=False,
            margin=dict(l=10, r=10, t=10, b=10),
        )
        st.plotly_chart(fig_creator_days, width="stretch")

        st.subheader("All listed managers")
        manager_goal_table = goal_manager_view[[
            "manager_name", "manager", "role", "group_name", "managed_creators", "diamonds", "diamond_goal",
            "diamond_progress", "new_creators", "new_creator_goal", "new_creator_progress"
        ]].copy()
        manager_goal_table.columns = [
            "Manager", "Manager email", "Role", "Group", "Managed creators", "Diamonds", "Diamond goal",
            "Diamond progress", "New creators", "New creator goal", "New creator progress"
        ]
        st.dataframe(
            manager_goal_table.sort_values("Diamonds", ascending=False),
            width="stretch",
            hide_index=True,
            column_config={
                "Diamonds": st.column_config.NumberColumn(format="localized"),
                "Diamond goal": st.column_config.NumberColumn(format="localized"),
                "Diamond progress": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
                "New creator progress": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
            },
        )

        st.subheader("Creators under each manager")
        tier_options = sorted(goal_creator_view["tier_status"].dropna().unique())
        selected_tiers = st.multiselect("Tier status", tier_options, key="goal_tier_status")
        displayed_goal_creators = goal_creator_view.copy()
        if selected_tiers:
            displayed_goal_creators = displayed_goal_creators[displayed_goal_creators["tier_status"].isin(selected_tiers)]
        creator_goal_table = displayed_goal_creators[[
            "manager_name", "manager", "username", "creator_id", "diamonds", "valid_live_days", "valid_live_hours",
            "tier_status", "activeness_level", "rank_up_progress", "group_name", "live_now"
        ]].copy()
        creator_goal_table["live_now"] = creator_goal_table["live_now"].map({1: "Yes", 0: "No"})
        creator_goal_table.columns = [
            "Manager", "Manager email", "Creator", "Creator ID", "Diamonds", "Valid LIVE days", "Valid LIVE hours",
            "Tier", "Activeness level", "Rank-up progress", "Group", "LIVE now"
        ]
        unassigned_mask = (creator_goal_table["Manager email"] == "Unassigned") | (creator_goal_table["Manager"].str.strip() == "")
        creator_goal_table.loc[unassigned_mask, "Manager"] = "Unassigned"
        creator_column_config = {
            "Diamonds": st.column_config.NumberColumn(format="localized"),
            "Valid LIVE hours": st.column_config.NumberColumn(format="%.0f h"),
        }
        creator_view_mode = st.radio(
            "Creator view",
            ["Grouped by manager", "All creators"],
            horizontal=True,
            label_visibility="collapsed",
            key="goal_creator_view_mode",
        )
        if creator_view_mode == "Grouped by manager":
            manager_sections = goal_manager_view[["manager", "manager_name", "diamonds", "diamond_goal"]].copy()
            manager_sections = manager_sections.sort_values("manager_name")
            section_rows = manager_sections.to_dict("records")
            if (creator_goal_table["Manager email"] == "Unassigned").any():
                section_rows.append({"manager": "Unassigned", "manager_name": "Unassigned", "diamonds": 0, "diamond_goal": 0})

            for section in section_rows:
                manager_email = section["manager"]
                manager_name = section["manager_name"] or manager_email
                manager_creators = creator_goal_table[creator_goal_table["Manager email"] == manager_email].copy()
                expander_label = f"{manager_name} — {len(manager_creators):,} creators"
                with st.expander(expander_label):
                    if manager_email != "Unassigned":
                        st.caption(
                            f"{manager_email} • {int(section['diamonds']):,} diamonds"
                            + (f" of {int(section['diamond_goal']):,} goal" if section["diamond_goal"] else " • goal not set")
                        )
                    if manager_creators.empty:
                        st.info("No creators are currently listed under this manager.")
                    else:
                        manager_display = manager_creators.drop(columns=["Manager", "Manager email", "Group"])
                        st.dataframe(
                            manager_display.sort_values("Diamonds", ascending=False),
                            width="stretch",
                            hide_index=True,
                            column_config=creator_column_config,
                        )
        else:
            st.dataframe(
                creator_goal_table.sort_values(["Manager", "Diamonds"], ascending=[True, False]),
                width="stretch",
                hide_index=True,
                column_config=creator_column_config,
            )
        st.download_button(
            "Download Goal management creators",
            creator_goal_table.to_csv(index=False).encode("utf-8"),
            "goal-management-creators.csv",
            "text/csv",
        )

with admin_tab:
    st.subheader("Update Backstage data")
    st.write("Upload the latest Creator data export from TikTok LIVE Backstage. The shared dashboard updates immediately after you confirm the preview.")

    admin_password = secret_value("ADMIN_PASSWORD")
    admin_allowed = True
    if admin_password:
        entered_password = st.text_input("Admin password", type="password", key="admin_update_password")
        admin_allowed = entered_password == admin_password
        if entered_password and not admin_allowed:
            st.error("The admin password is not correct.")
    else:
        st.warning("ADMIN_PASSWORD is not configured. Every invited dashboard viewer can update the data.")

    if not latest_update.empty:
        update = latest_update.iloc[0]
        update_time = pd.to_datetime(update["updated_at"], errors="coerce")
        update_label = update_time.strftime("%b %d, %Y %I:%M %p UTC") if pd.notna(update_time) else "Unknown time"
        st.info(f"Current shared data: {int(update['creator_rows']):,} creators • updated {update_label}")
    else:
        st.info("No Creator data export has been uploaded yet.")

    st.write("In Backstage, open **Data → Creator data → Export**, then upload that Excel file below.")
    st.caption("The upload updates diamonds, valid LIVE days, valid LIVE hours, tier status, and each creator’s manager. Existing manager goals are preserved when the manager is present in the new file.")

    uploaded_export = st.file_uploader("Backstage Creator data export", type=["xlsx", "csv"], disabled=not admin_allowed)
    parsed_creators = None
    parsed_managers = None
    if uploaded_export is not None and admin_allowed:
        try:
            parsed_creators, parsed_managers = parse_backstage_export(uploaded_export)
            u1, u2, u3 = st.columns(3)
            u1.metric("Creators found", f"{len(parsed_creators):,}")
            u2.metric("Assigned managers", f"{len(parsed_managers):,}")
            u3.metric("Unassigned creators", f"{(parsed_creators['manager'] == 'Unassigned').sum():,}")
            st.dataframe(
                parsed_creators[["manager_name", "username", "diamonds", "valid_live_days", "valid_live_hours", "tier_status"]].head(25),
                width="stretch",
                hide_index=True,
            )
        except Exception as exc:
            st.error(str(exc))

    confirm_replace = st.checkbox(
        "I understand this will replace the current shared creator data.",
        disabled=parsed_creators is None or not admin_allowed,
    )
    if st.button(
        "Update shared dashboard",
        type="primary",
        disabled=parsed_creators is None or not confirm_replace or not admin_allowed,
    ):
        replace_goal_data(parsed_creators, parsed_managers, uploaded_export.name)
        load_manager_performance.clear()
        load_goal_managers.clear()
        load_goal_creators.clear()
        load_latest_update.clear()
        st.session_state["update_complete"] = f"Updated {len(parsed_creators):,} creators from {uploaded_export.name}."
        st.rerun()
    if st.session_state.pop("update_complete", None):
        st.success("The shared dashboard was updated successfully.")

with roster_tab:
    total = len(filtered)
    active = filtered["status"].str.lower().isin(["active", "approved", "enabled"]).sum()
    inactive = filtered["status"].str.lower().isin(["inactive", "disabled", "suspended", "rejected"]).sum()
    new_this_month = 0
    if "joined_at" in filtered:
        now = pd.Timestamp.now(tz=None)
        new_this_month = ((filtered["joined_at"].dt.year == now.year) & (filtered["joined_at"].dt.month == now.month)).sum()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Creators", f"{total:,}")
    c2.metric("Active/approved", f"{active:,}")
    c3.metric("Inactive/problem", f"{inactive:,}")
    c4.metric("Joined this month", f"{new_this_month:,}")

    st.subheader("Creator accounts")
    preferred = ["creator_id", "creator_name", "username", "status", "agency", "manager", "country", "joined_at", "last_active_at"]
    display_columns = [column for column in preferred if column in filtered.columns]
    st.dataframe(filtered[display_columns], width="stretch", hide_index=True)
    csv_bytes = filtered[display_columns].to_csv(index=False).encode("utf-8")
    st.download_button("Download filtered CSV", csv_bytes, "creators.csv", "text/csv")
