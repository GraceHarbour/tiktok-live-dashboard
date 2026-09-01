from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from sqlalchemy import text


MILESTONES = [
    (5_000_000, 15, 30, "TikTok Universe", 44_999),
    (2_000_000, 15, 30, "TikTok Stars", 39_999),
    (1_500_000, 15, 30, "Dragon Flame", 26_999),
    (1_000_000, 15, 30, "Adam's Dream", 25_999),
    (500_000, 10, 20, "Interstellar", 10_000),
    (300_000, 8, 20, "Leon the Kitten", 4_888),
    (150_000, 8, 20, "Motorcycle", 2_988),
]


def _ensure_tables(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS monthly_reward_events (
                id bigserial PRIMARY KEY,
                month_key text NOT NULL,
                event_name text NOT NULL,
                scheduled_at timestamptz NOT NULL,
                manager_name text NOT NULL DEFAULT '',
                created_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS monthly_reward_event_creators (
                event_id bigint NOT NULL REFERENCES monthly_reward_events(id) ON DELETE CASCADE,
                creator_id text NOT NULL,
                username text NOT NULL,
                manager_name text NOT NULL DEFAULT '',
                reward_name text NOT NULL,
                reward_value bigint NOT NULL,
                qualified_milestone bigint NOT NULL,
                delivered boolean NOT NULL DEFAULT false,
                delivered_at timestamptz,
                entry_source text NOT NULL DEFAULT 'automatic',
                PRIMARY KEY (event_id, creator_id)
            )
        """))
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS monthly_reward_results (
                month_key text NOT NULL,
                creator_id text NOT NULL,
                username text NOT NULL,
                manager_name text NOT NULL DEFAULT '',
                diamonds bigint NOT NULL DEFAULT 0,
                valid_live_days integer NOT NULL DEFAULT 0,
                valid_live_hours numeric NOT NULL DEFAULT 0,
                maintained_or_ranked boolean NOT NULL DEFAULT false,
                reward_name text NOT NULL DEFAULT '',
                reward_value bigint NOT NULL DEFAULT 0,
                qualified_milestone bigint NOT NULL DEFAULT 0,
                eligible boolean NOT NULL DEFAULT false,
                disqualification_reason text NOT NULL DEFAULT '',
                captured_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (month_key, creator_id)
            )
        """))


def _series(frame: pd.DataFrame, column: str, default=0) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index)
    return pd.to_numeric(frame[column], errors="coerce").fillna(default)


def _manager(frame: pd.DataFrame) -> pd.Series:
    for column in ("manager_name", "manager"):
        if column in frame.columns:
            values = frame[column].fillna("").astype(str).str.strip()
            if values.ne("").any():
                return values.replace("", "Unassigned")
    return pd.Series("Unassigned", index=frame.index)


def _tier_eligible(frame: pd.DataFrame) -> pd.Series:
    tier = frame.get("tier_status", pd.Series("", index=frame.index)).fillna("").astype(str).str.casefold()
    rank = frame.get("rank_up_progress", pd.Series("", index=frame.index)).fillna("").astype(str).str.casefold()
    combined = tier + " " + rank
    explicit_not = combined.str.contains(r"not\s+maintain", regex=True, na=False)
    maintained = combined.str.contains(r"maintained|maintaining|maintain", regex=True, na=False) & ~explicit_not
    ranked = combined.str.contains(r"ranked\s*up|ranking\s*up|rank\s*up", regex=True, na=False)
    return maintained | ranked


def _classify(frame: pd.DataFrame) -> pd.DataFrame:
    rows = frame.copy().reset_index(drop=True)
    rows["Creator ID"] = rows.get("creator_id", pd.Series("", index=rows.index)).fillna("").astype(str)
    rows["Creator"] = rows.get("username", rows["Creator ID"]).fillna("").astype(str)
    rows["Manager"] = _manager(rows)
    rows["Diamonds"] = _series(rows, "diamonds").astype("int64")
    rows["Valid LIVE days"] = _series(rows, "valid_live_days").astype("int64")
    rows["Valid LIVE hours"] = _series(rows, "valid_live_hours").round(1)
    rows["Maintained or ranked up"] = _tier_eligible(rows)
    photo_column = next((c for c in ("avatar_url", "profile_image_url", "photo_url", "image_url") if c in rows), None)
    rows["Picture"] = rows[photo_column].fillna("").astype(str) if photo_column else ""

    classifications = []
    for row in rows.to_dict("records"):
        reached = next((m for m in MILESTONES if row["Diamonds"] >= m[0]), None)
        if not reached:
            classifications.append(("", 0, 0, False, "Below 150,000-diamond reward level"))
            continue
        diamond_level, days_needed, hours_needed, reward_name, reward_value = reached
        reasons = []
        if not row["Maintained or ranked up"]:
            reasons.append("Did not maintain or rank up tier")
        if row["Valid LIVE days"] < days_needed:
            reasons.append(f"Needs {days_needed} valid LIVE days")
        if row["Valid LIVE hours"] < hours_needed:
            reasons.append(f"Needs {hours_needed} valid LIVE hours")
        classifications.append((reward_name, reward_value, diamond_level, not reasons, "; ".join(reasons)))
    classified = pd.DataFrame(classifications, columns=["Reward", "Prize value", "Milestone", "Eligible", "Missing requirement"])
    return pd.concat([rows, classified], axis=1)


def _display_table(frame: pd.DataFrame, height: int = 480) -> None:
    config = {
        "Picture": st.column_config.ImageColumn("Picture", width="small"),
        "Diamonds": st.column_config.NumberColumn(format="%,d"),
        "Milestone": st.column_config.NumberColumn(format="%,d"),
        "Prize value": st.column_config.NumberColumn(format="%,d coins"),
    }
    st.dataframe(frame, use_container_width=True, hide_index=True, height=height, column_config=config)


def _event_section(engine, classified: pd.DataFrame, manager_names: list[str], month_key: str) -> None:
    st.markdown("### Reward distribution events")
    st.caption("Schedule a reward session. Every currently eligible creator is automatically added with the single highest reward earned.")
    eastern = ZoneInfo("America/New_York")
    now = dt.datetime.now(eastern)
    time_options = [
        dt.datetime(2000, 1, 1, hour, minute).strftime("%-I:%M %p")
        for hour in range(24) for minute in (0, 15, 30, 45)
    ]
    with st.form("monthly_reward_event_form", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([1.5, 1, 1, 1])
        event_name = c1.text_input("Event name", placeholder="September Mission Rewards")
        event_date = c2.date_input("Event date", value=now.date())
        default_time = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0).strftime("%-I:%M %p")
        event_time = c3.selectbox("Event time", time_options, index=time_options.index(default_time))
        event_manager = c4.selectbox("Manager", ["All managers", *manager_names])
        create_event = st.form_submit_button("Create reward event", type="primary")
    if create_event:
        clean_name = event_name.strip()
        if not clean_name:
            st.error("Enter an event name.")
        else:
            parsed_time = dt.datetime.strptime(event_time, "%I:%M %p").time()
            scheduled = dt.datetime.combine(event_date, parsed_time, tzinfo=eastern)
            auto_rows = classified[classified["Eligible"]].copy()
            if event_manager != "All managers":
                auto_rows = auto_rows[auto_rows["Manager"] == event_manager]
            with engine.begin() as connection:
                event_id = connection.execute(text("""
                    INSERT INTO monthly_reward_events(month_key,event_name,scheduled_at,manager_name)
                    VALUES (:month_key,:event_name,:scheduled_at,:manager_name) RETURNING id
                """), {"month_key": month_key, "event_name": clean_name, "scheduled_at": scheduled, "manager_name": event_manager}).scalar_one()
                for row in auto_rows.to_dict("records"):
                    connection.execute(text("""
                        INSERT INTO monthly_reward_event_creators
                            (event_id,creator_id,username,manager_name,reward_name,reward_value,qualified_milestone,entry_source)
                        VALUES (:event_id,:creator_id,:username,:manager_name,:reward_name,:reward_value,:milestone,'automatic')
                        ON CONFLICT(event_id,creator_id) DO NOTHING
                    """), {"event_id": event_id, "creator_id": row["Creator ID"], "username": row["Creator"], "manager_name": row["Manager"], "reward_name": row["Reward"], "reward_value": int(row["Prize value"]), "milestone": int(row["Milestone"])})
            st.success(f"Created {clean_name} with {len(auto_rows):,} eligible creators.")
            st.rerun()

    events = pd.read_sql(text("SELECT id,month_key,event_name,scheduled_at,manager_name FROM monthly_reward_events ORDER BY scheduled_at DESC,id DESC"), engine)
    if events.empty:
        st.info("No reward distribution events have been scheduled yet.")
        return
    events["label"] = events.apply(lambda r: f"{r['event_name']} — {pd.Timestamp(r['scheduled_at']).tz_convert(eastern).strftime('%b %-d, %Y at %-I:%M %p')} ({r['manager_name']})", axis=1)
    selected_label = st.selectbox("View reward event", events["label"].tolist())
    event = events[events["label"] == selected_label].iloc[0]
    event_id = int(event["id"])

    add_manager = st.selectbox("Filter creators to add", ["All managers", *manager_names], key=f"reward_add_manager_{event_id}")
    add_pool = classified[classified["Eligible"]].copy()
    if add_manager != "All managers":
        add_pool = add_pool[add_pool["Manager"] == add_manager]
    label_map = {f"{r['Creator']} — {r['Manager']} — {r['Reward']}": r for r in add_pool.to_dict("records")}
    selected_add = st.multiselect("Add creators after scheduling", list(label_map), key=f"reward_add_{event_id}")
    if st.button("Add selected creators", key=f"reward_add_button_{event_id}", disabled=not selected_add):
        with engine.begin() as connection:
            for label in selected_add:
                row = label_map[label]
                connection.execute(text("""
                    INSERT INTO monthly_reward_event_creators
                        (event_id,creator_id,username,manager_name,reward_name,reward_value,qualified_milestone,entry_source)
                    VALUES (:event_id,:creator_id,:username,:manager_name,:reward_name,:reward_value,:milestone,'manual')
                    ON CONFLICT(event_id,creator_id) DO NOTHING
                """), {"event_id": event_id, "creator_id": row["Creator ID"], "username": row["Creator"], "manager_name": row["Manager"], "reward_name": row["Reward"], "reward_value": int(row["Prize value"]), "milestone": int(row["Milestone"])})
        st.rerun()

    people = pd.read_sql(text("""
        SELECT creator_id,username,manager_name,reward_name,reward_value,qualified_milestone,delivered,entry_source
        FROM monthly_reward_event_creators WHERE event_id=:event_id ORDER BY reward_value DESC,username
    """), engine, params={"event_id": event_id})
    if people.empty:
        st.warning("No creators are assigned to this reward event.")
        return
    original = people.set_index("creator_id")["delivered"].astype(bool)
    editor = people.rename(columns={"username":"Creator","manager_name":"Manager","reward_name":"Reward","reward_value":"Prize value","qualified_milestone":"Milestone","delivered":"Given out","entry_source":"Added"})
    edited = st.data_editor(editor, use_container_width=True, hide_index=True, disabled=["creator_id","Creator","Manager","Reward","Prize value","Milestone","Added"], column_config={"Given out": st.column_config.CheckboxColumn("Given out"), "Prize value": st.column_config.NumberColumn(format="%,d coins"), "Milestone": st.column_config.NumberColumn(format="%,d")}, key=f"reward_editor_{event_id}")
    if st.button("Save payout updates", key=f"reward_save_{event_id}", type="primary"):
        with engine.begin() as connection:
            for row in edited.to_dict("records"):
                delivered = bool(row["Given out"])
                if delivered != bool(original.get(row["creator_id"], False)):
                    connection.execute(text("""
                        UPDATE monthly_reward_event_creators SET delivered=:delivered,
                            delivered_at=CASE WHEN :delivered THEN now() ELSE NULL END
                        WHERE event_id=:event_id AND creator_id=:creator_id
                    """), {"delivered": delivered, "event_id": event_id, "creator_id": row["creator_id"]})
        st.success("Payout status saved.")
        st.rerun()
    remove_labels = {f"{r['Creator']} — {r['Reward']}": r["creator_id"] for r in editor.to_dict("records")}
    selected_remove = st.multiselect("Remove creators from this event", list(remove_labels), key=f"reward_remove_{event_id}")
    if st.button("Remove selected creators", key=f"reward_remove_button_{event_id}", disabled=not selected_remove):
        with engine.begin() as connection:
            for label in selected_remove:
                connection.execute(text("DELETE FROM monthly_reward_event_creators WHERE event_id=:event_id AND creator_id=:creator_id"), {"event_id": event_id, "creator_id": remove_labels[label]})
        st.rerun()


def render_monthly_mission_rewards(engine, creators: pd.DataFrame, manager_names: list[str]) -> None:
    _ensure_tables(engine)
    st.subheader("Monthly Mission Rewards")
    st.caption("Rewards are calculated from the final successful 7:59 PM ET Goal read. Creators receive only their single highest completed milestone and must maintain or rank up their tier.")
    classified = _classify(creators)
    current_month = dt.datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m")
    finalized_months = pd.read_sql(text("SELECT DISTINCT month_key FROM monthly_reward_results ORDER BY month_key DESC"), engine)["month_key"].astype(str).tolist()
    month_options = list(dict.fromkeys([current_month, *finalized_months]))
    selected_month = st.selectbox("Reward month", month_options, format_func=lambda value: pd.Timestamp(f"{value}-01").strftime("%B %Y"))
    if selected_month != current_month:
        all_finalized = pd.read_sql(text("SELECT * FROM monthly_reward_results"), engine)
        finalized = all_finalized[all_finalized["month_key"].astype(str) == selected_month].copy()
        classified = finalized.rename(columns={
            "creator_id":"Creator ID", "username":"Creator", "manager_name":"Manager",
            "diamonds":"Diamonds", "valid_live_days":"Valid LIVE days",
            "valid_live_hours":"Valid LIVE hours", "maintained_or_ranked":"Maintained or ranked up",
            "reward_name":"Reward", "reward_value":"Prize value",
            "qualified_milestone":"Milestone", "eligible":"Eligible",
            "disqualification_reason":"Missing requirement",
        })
        classified["Picture"] = ""

    c1, c2 = st.columns([1, 1.5])
    manager = c1.selectbox("Manager", ["All managers", *manager_names], key="mission_reward_manager")
    search = c2.text_input("Search creators", key="mission_reward_search").strip()
    if manager != "All managers":
        classified = classified[classified["Manager"] == manager]
    if search:
        classified = classified[classified["Creator"].str.contains(re.escape(search), case=False, na=False)]

    qualified = classified[classified["Eligible"]].copy()
    near = classified[(classified["Milestone"] > 0) & ~classified["Eligible"]].copy()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Eligible creators", f"{len(qualified):,}")
    m2.metric("Prize value", f"{int(qualified['Prize value'].sum()):,} coins")
    m3.metric("Reached diamonds but ineligible", f"{len(near):,}")
    m4.metric("Highest milestone", f"{int(qualified['Milestone'].max()):,}" if not qualified.empty else "None")

    with st.expander("Reward milestone requirements"):
        requirements = pd.DataFrame([{"Diamonds":m[0],"Valid LIVE days":m[1],"Valid LIVE hours":m[2],"Reward":m[3],"Prize value":m[4]} for m in reversed(MILESTONES)])
        _display_table(requirements, 320)

    st.markdown("### Eligible reward winners")
    if qualified.empty:
        st.info("No creators currently meet every reward requirement.")
    else:
        qualified = qualified.sort_values(["Milestone","Diamonds","Creator"], ascending=[False,False,True])
        _display_table(qualified[["Picture","Creator","Manager","Diamonds","Valid LIVE days","Valid LIVE hours","Maintained or ranked up","Reward","Milestone","Prize value"]], min(760, 88 + len(qualified) * 44))

    st.markdown("### Reached a reward diamond level but did not qualify")
    st.caption("These creators reached a reward's diamond threshold but missed tier maintenance, LIVE days, or LIVE hours.")
    if near.empty:
        st.success("No creators are currently disqualified after reaching a reward diamond level.")
    else:
        near = near.sort_values(["Milestone","Diamonds","Creator"], ascending=[False,False,True])
        _display_table(near[["Picture","Creator","Manager","Diamonds","Valid LIVE days","Valid LIVE hours","Maintained or ranked up","Reward","Milestone","Prize value","Missing requirement"]], min(760, 88 + len(near) * 48))

    _event_section(engine, classified, manager_names, selected_month)
