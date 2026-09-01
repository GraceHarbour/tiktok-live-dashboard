from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
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
                ends_at timestamptz,
                manager_name text NOT NULL DEFAULT '',
                created_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        connection.execute(text("ALTER TABLE monthly_reward_events ADD COLUMN IF NOT EXISTS ends_at timestamptz"))
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
                met_requirements text NOT NULL DEFAULT '',
                avatar_url text NOT NULL DEFAULT '',
                track boolean NOT NULL DEFAULT true,
                captured_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (month_key, creator_id)
            )
        """))
        connection.execute(text("ALTER TABLE monthly_reward_results ADD COLUMN IF NOT EXISTS met_requirements text NOT NULL DEFAULT ''"))
        connection.execute(text("ALTER TABLE monthly_reward_results ADD COLUMN IF NOT EXISTS avatar_url text NOT NULL DEFAULT ''"))
        connection.execute(text("ALTER TABLE monthly_reward_results ADD COLUMN IF NOT EXISTS track boolean NOT NULL DEFAULT true"))
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS monthly_reward_unavailable (
                month_key text NOT NULL,
                creator_id text NOT NULL,
                username text NOT NULL,
                manager_name text NOT NULL DEFAULT '',
                reward_name text NOT NULL DEFAULT '',
                event_id bigint REFERENCES monthly_reward_events(id) ON DELETE SET NULL,
                marked_at timestamptz NOT NULL DEFAULT now(),
                override_granted boolean NOT NULL DEFAULT false,
                overridden_at timestamptz,
                overridden_by text NOT NULL DEFAULT '',
                reschedule_blocked boolean NOT NULL DEFAULT false,
                reschedule_count integer NOT NULL DEFAULT 0,
                PRIMARY KEY (month_key, creator_id)
            )
        """))
        connection.execute(text("ALTER TABLE monthly_reward_unavailable ADD COLUMN IF NOT EXISTS override_granted boolean NOT NULL DEFAULT false"))
        connection.execute(text("ALTER TABLE monthly_reward_unavailable ADD COLUMN IF NOT EXISTS overridden_at timestamptz"))
        connection.execute(text("ALTER TABLE monthly_reward_unavailable ADD COLUMN IF NOT EXISTS overridden_by text NOT NULL DEFAULT ''"))
        connection.execute(text("ALTER TABLE monthly_reward_unavailable ADD COLUMN IF NOT EXISTS reschedule_blocked boolean NOT NULL DEFAULT false"))
        connection.execute(text("ALTER TABLE monthly_reward_unavailable ADD COLUMN IF NOT EXISTS reschedule_count integer NOT NULL DEFAULT 0"))


def _signed_in_reward_approver(engine) -> tuple[bool, str]:
    try:
        email = str(st.context.headers.get("X-Goog-Authenticated-User-Email", "")).strip()
    except Exception:
        email = ""
    if ":" in email:
        email = email.split(":", 1)[1]
    email = email.casefold()
    if not email:
        return False, ""
    with engine.connect() as connection:
        role = connection.execute(text("SELECT role FROM dashboard_access_people WHERE email=:email AND active=true"), {"email": email}).scalar()
    return str(role or "").casefold() in {"member", "owner", "admin"}, email


def _series(frame: pd.DataFrame, column: str, default=0) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index)
    return pd.to_numeric(frame[column], errors="coerce").fillna(default)


def _live_hours(frame: pd.DataFrame) -> pd.Series:
    if "valid_live_hours" in frame:
        return _series(frame, "valid_live_hours").round(1)
    duration = frame.get("valid_live_duration", pd.Series("", index=frame.index)).fillna("").astype(str)
    hours = duration.str.extract(r"(?:(\d+)h)", expand=False).fillna(0).astype(float)
    minutes = duration.str.extract(r"(?:(\d+)m)", expand=False).fillna(0).astype(float)
    seconds = duration.str.extract(r"(?:(\d+)s)", expand=False).fillna(0).astype(float)
    return (hours + minutes / 60 + seconds / 3600).round(1)


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
    rows["Valid LIVE hours"] = _live_hours(rows)
    rows["Maintained or ranked up"] = _tier_eligible(rows)
    photo_column = next((c for c in ("avatar_url", "profile_image_url", "photo_url", "image_url") if c in rows), None)
    rows["Picture"] = rows[photo_column].fillna("").astype(str) if photo_column else ""

    classifications = []
    lowest_milestone = MILESTONES[-1]
    for row in rows.to_dict("records"):
        reached = next((m for m in MILESTONES if row["Diamonds"] >= m[0]), None)
        diamond_level, days_needed, hours_needed, reward_name, reward_value = reached or lowest_milestone
        met = []
        missing = []
        if row["Diamonds"] >= diamond_level:
            met.append(f"Diamonds: {row['Diamonds']:,} / {diamond_level:,}")
        else:
            missing.append(f"Diamonds: needs {diamond_level:,}")
        if row["Maintained or ranked up"]:
            met.append("Maintained or ranked up tier")
        else:
            missing.append("Must maintain or rank up tier")
        if row["Valid LIVE days"] >= days_needed:
            met.append(f"LIVE days: {row['Valid LIVE days']} / {days_needed}")
        else:
            missing.append(f"LIVE days: {row['Valid LIVE days']} / {days_needed}")
        if row["Valid LIVE hours"] >= hours_needed:
            met.append(f"LIVE hours: {row['Valid LIVE hours']:g} / {hours_needed}")
        else:
            missing.append(f"LIVE hours: {row['Valid LIVE hours']:g} / {hours_needed}")
        eligible = not missing
        track = row["Diamonds"] >= lowest_milestone[0]
        classifications.append(
            (reward_name, reward_value, diamond_level, eligible, track, "; ".join(met), "; ".join(missing))
        )
    classified = pd.DataFrame(
        classifications,
        columns=["Reward", "Prize value", "Milestone", "Eligible", "Track", "Met requirements", "Missing requirements"],
    )
    return pd.concat([rows, classified], axis=1)


def _display_table(frame: pd.DataFrame, height: int = 480) -> None:
    config = {
        "Picture": st.column_config.ImageColumn("Picture", width="small"),
        "Diamonds": st.column_config.NumberColumn(format="%,d"),
        "Milestone": st.column_config.NumberColumn(format="%,d"),
        "Prize value": st.column_config.NumberColumn(format="%,d coins"),
    }
    styled = frame.style.set_properties(**{
        "background-color": "#0b2342", "color": "#f7fbff",
        "border-color": "#315b86", "font-weight": "500",
    }).set_table_styles([
        {"selector": "th", "props": [("background-color", "#123f70"), ("color", "white"), ("font-weight", "700")]},
    ])
    st.dataframe(styled, use_container_width=True, hide_index=True, height=height, column_config=config)


def _event_section(engine, classified: pd.DataFrame, manager_names: list[str], month_key: str) -> None:
    st.markdown("### Reward distribution events")
    st.caption("Schedule as many reward sessions as needed. Managers can add their eligible creators to an event, or use their one unavailable allowance for the month.")
    eastern = ZoneInfo("America/New_York")
    now = dt.datetime.now(eastern)
    time_options = [
        dt.datetime(2000, 1, 1, hour, minute).strftime("%-I:%M %p")
        for hour in range(24) for minute in (0, 15, 30, 45)
    ]
    with st.form("monthly_reward_event_form", clear_on_submit=True):
        c1, c2, c3, c4, c5 = st.columns([1.5, 1, 1, 1, 1])
        event_name = c1.text_input("Event name", placeholder="September Mission Rewards")
        event_date = c2.date_input("Event date", value=now.date())
        default_time = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0).strftime("%-I:%M %p")
        start_index = time_options.index(default_time)
        event_start_time = c3.selectbox("Start time", time_options, index=start_index)
        event_end_time = c4.selectbox("End time", time_options, index=min(start_index + 4, len(time_options) - 1))
        event_manager = c5.selectbox("Manager", ["All managers", *manager_names])
        create_event = st.form_submit_button("Create reward event", type="primary")
    if create_event:
        clean_name = event_name.strip()
        if not clean_name:
            st.error("Enter an event name.")
        else:
            start_time = dt.datetime.strptime(event_start_time, "%I:%M %p").time()
            end_time = dt.datetime.strptime(event_end_time, "%I:%M %p").time()
            scheduled = dt.datetime.combine(event_date, start_time, tzinfo=eastern)
            ends_at = dt.datetime.combine(event_date, end_time, tzinfo=eastern)
            if ends_at <= scheduled:
                st.error("End time must be later than the start time.")
            else:
                with engine.begin() as connection:
                    connection.execute(text("""
                        INSERT INTO monthly_reward_events(month_key,event_name,scheduled_at,ends_at,manager_name)
                        VALUES (:month_key,:event_name,:scheduled_at,:ends_at,:manager_name)
                    """), {"month_key": month_key, "event_name": clean_name, "scheduled_at": scheduled, "ends_at": ends_at, "manager_name": event_manager})
                st.success(f"Created {clean_name}. Managers can now add unscheduled eligible creators.")
                st.rerun()

    events = pd.read_sql(text("SELECT id,month_key,event_name,scheduled_at,ends_at,manager_name FROM monthly_reward_events WHERE month_key=:month_key ORDER BY scheduled_at,id"), engine, params={"month_key": month_key})
    if events.empty:
        st.info("No reward distribution events have been scheduled yet.")
        return
    events["label"] = events.apply(lambda r: f"{r['event_name']} — {pd.Timestamp(r['scheduled_at']).tz_convert(eastern).strftime('%b %-d, %Y, %-I:%M %p')} to {pd.Timestamp(r['ends_at']).tz_convert(eastern).strftime('%-I:%M %p') if pd.notna(r['ends_at']) else 'end time not set'} ({r['manager_name']})", axis=1)
    selected_label = st.selectbox("View reward event", events["label"].tolist())
    event = events[events["label"] == selected_label].iloc[0]
    event_id = int(event["id"])

    add_manager = st.selectbox("Filter creators to add", ["All managers", *manager_names], key=f"reward_add_manager_{event_id}")
    add_pool = classified[classified["Eligible"]].copy()
    if add_manager != "All managers":
        add_pool = add_pool[add_pool["Manager"] == add_manager]
    already_assigned = pd.read_sql(text("""
        SELECT DISTINCT ec.creator_id FROM monthly_reward_event_creators ec
        JOIN monthly_reward_events e ON e.id=ec.event_id WHERE e.month_key=:month_key
    """), engine, params={"month_key": month_key})
    if not already_assigned.empty:
        add_pool = add_pool[~add_pool["Creator ID"].isin(already_assigned["creator_id"].astype(str))]
    unavailable_locked = pd.read_sql(text("SELECT creator_id FROM monthly_reward_unavailable WHERE month_key=:month_key AND (override_granted=false OR reschedule_blocked=true)"), engine, params={"month_key": month_key})
    if not unavailable_locked.empty:
        add_pool = add_pool[~add_pool["Creator ID"].isin(unavailable_locked["creator_id"].astype(str))]
    st.markdown("#### Creators still needing an event")
    if add_pool.empty:
        st.success("Every currently eligible creator is scheduled, delivered, or awaiting manager reschedule approval.")
    else:
        needs_columns = [column for column in ["Picture", "Creator", "Manager", "Reward", "Milestone", "Prize value"] if column in add_pool]
        _display_table(add_pool[needs_columns].sort_values(["Manager", "Creator"]), min(480, 88 + len(add_pool) * 42))
    label_map = {f"{r['Creator']} — {r['Manager']} — {r['Reward']}": r for r in add_pool.to_dict("records")}
    selected_add = st.multiselect("Add unscheduled creators from the eligible rewards list", list(label_map), key=f"reward_add_{event_id}")
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
        st.warning("No creators are assigned to this reward event yet.")
    else:
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

        active_rows = editor[~editor["Given out"].astype(bool)].copy()
        unavailable_labels = {f"{r['Creator']} — {r['Manager']} — {r['Reward']}": r for r in active_rows.to_dict("records")}
        selected_unavailable = st.multiselect("Select creators to reschedule / mark unavailable", list(unavailable_labels), key=f"reward_unavailable_{event_id}")
        if st.button("Remove from this event and reschedule", key=f"reward_unavailable_button_{event_id}", disabled=not selected_unavailable):
            already_used = []
            moved = 0
            with engine.begin() as connection:
                for label in selected_unavailable:
                    row = unavailable_labels[label]
                    prior = connection.execute(text("SELECT 1 FROM monthly_reward_unavailable WHERE month_key=:month_key AND creator_id=:creator_id"), {"month_key": month_key, "creator_id": row["creator_id"]}).first()
                    if prior:
                        connection.execute(text("""
                            UPDATE monthly_reward_unavailable SET reschedule_blocked=true,
                                override_granted=false,marked_at=now(),event_id=:event_id,
                                reschedule_count=reschedule_count+1
                            WHERE month_key=:month_key AND creator_id=:creator_id AND override_granted=true
                        """), {"event_id": event_id, "month_key": month_key, "creator_id": row["creator_id"]})
                        connection.execute(text("DELETE FROM monthly_reward_event_creators WHERE event_id=:event_id AND creator_id=:creator_id"), {"event_id": event_id, "creator_id": row["creator_id"]})
                        already_used.append(row["Creator"] + " — manager approval required")
                        continue
                    connection.execute(text("""
                        INSERT INTO monthly_reward_unavailable(month_key,creator_id,username,manager_name,reward_name,event_id,override_granted,reschedule_count)
                        VALUES (:month_key,:creator_id,:username,:manager_name,:reward_name,:event_id,true,1)
                    """), {"month_key": month_key, "creator_id": row["creator_id"], "username": row["Creator"], "manager_name": row["Manager"], "reward_name": row["Reward"], "event_id": event_id})
                    connection.execute(text("DELETE FROM monthly_reward_event_creators WHERE event_id=:event_id AND creator_id=:creator_id"), {"event_id": event_id, "creator_id": row["creator_id"]})
                    moved += 1
            if already_used:
                st.error("These creators used their automatic reschedule and now require manager approval before another event: " + ", ".join(already_used))
            if moved:
                st.success(f"Removed {moved} creator(s) from this event and returned them to Creators still needing an event. Their first reschedule is automatic; later reschedules require manager approval.")
                st.rerun()

        remove_labels = {f"{r['Creator']} — {r['Reward']}": r["creator_id"] for r in editor.to_dict("records")}
        selected_remove = st.multiselect("Remove creators from this event", list(remove_labels), key=f"reward_remove_{event_id}")
        if st.button("Remove selected creators", key=f"reward_remove_button_{event_id}", disabled=not selected_remove):
            with engine.begin() as connection:
                for label in selected_remove:
                    connection.execute(text("DELETE FROM monthly_reward_event_creators WHERE event_id=:event_id AND creator_id=:creator_id"), {"event_id": event_id, "creator_id": remove_labels[label]})
            st.rerun()

    st.markdown("#### Rescheduled / unavailable creators")
    unavailable = pd.read_sql(text("""
        SELECT u.creator_id,u.username AS "Creator",u.manager_name AS "Manager",u.reward_name AS "Reward",
               e.event_name AS "Unavailable for event",u.marked_at AS "Marked unavailable",
               u.reschedule_count AS "Times rescheduled",u.override_granted AS "Reschedule approved",
               u.reschedule_blocked AS "Manager approval required",
               u.overridden_by AS "Approved by"
        FROM monthly_reward_unavailable u LEFT JOIN monthly_reward_events e ON e.id=u.event_id
        WHERE u.month_key=:month_key ORDER BY u.marked_at DESC
    """), engine, params={"month_key": month_key})
    if unavailable.empty:
        st.info("No creators have used their unavailable allowance this month.")
    else:
        _display_table(unavailable.drop(columns=["creator_id"]), min(440, 88 + len(unavailable) * 42))
        can_override, actor_email = _signed_in_reward_approver(engine)
        locked = unavailable[(~unavailable["Reschedule approved"].astype(bool)) & unavailable["Manager approval required"].astype(bool)]
        if can_override and not locked.empty:
            override_map = {f"{r['Creator']} — {r['Manager']} — {r['Reward']}": r["creator_id"] for r in locked.to_dict("records")}
            selected_override = st.multiselect("Manager reschedule approval", list(override_map), key=f"reward_override_{month_key}")
            if st.button("Manager approve selected for rescheduling", key=f"reward_override_button_{month_key}", disabled=not selected_override):
                with engine.begin() as connection:
                    for label in selected_override:
                        connection.execute(text("""
                            UPDATE monthly_reward_unavailable SET override_granted=true,
                                reschedule_blocked=false,overridden_at=now(),overridden_by=:actor
                            WHERE month_key=:month_key AND creator_id=:creator_id
                        """), {"actor": actor_email, "month_key": month_key, "creator_id": override_map[label]})
                st.success("Manager approval saved. The selected creator(s) can now be added to another event.")
                st.rerun()
        elif not can_override and not locked.empty:
            st.caption("A signed-in manager must approve these creators before they can be rescheduled.")


def _render_mission_rewards(engine, creators: pd.DataFrame, manager_names: list[str]) -> None:
    _ensure_tables(engine)
    st.subheader("Monthly Mission Rewards")
    st.caption("During the month, progress uses the latest Goal read. Final monthly results use the complete Creator Data report captured after 8:00 AM ET on the first. Creators below 150,000 diamonds are excluded.")
    reward_banner = Path(__file__).resolve().parent / "assets" / "monthly-milestone-rewards.jpg"
    if reward_banner.exists():
        st.image(str(reward_banner), use_container_width=True)
    classified = _classify(creators)
    current_month = dt.datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m")
    finalized_months = pd.read_sql(text("SELECT DISTINCT month_key FROM monthly_reward_results ORDER BY month_key DESC"), engine)["month_key"].astype(str).tolist()
    month_options = list(dict.fromkeys([current_month, *finalized_months]))
    selected_month = st.selectbox("Reward month", month_options, format_func=lambda value: pd.Timestamp(f"{value}-01").strftime("%B %Y"))
    live_progress = selected_month == current_month
    all_snapshots = pd.read_sql(text("SELECT * FROM monthly_reward_results"), engine)
    finalized = all_snapshots[all_snapshots["month_key"].astype(str) == selected_month].copy()
    using_creator_data = not finalized.empty
    if using_creator_data:
        classified = finalized.rename(columns={
            "creator_id":"Creator ID", "username":"Creator", "manager_name":"Manager",
            "diamonds":"Diamonds", "valid_live_days":"Valid LIVE days",
            "valid_live_hours":"Valid LIVE hours", "maintained_or_ranked":"Maintained or ranked up",
            "reward_name":"Reward", "reward_value":"Prize value",
            "qualified_milestone":"Milestone", "eligible":"Eligible",
            "disqualification_reason":"Missing requirement",
            "met_requirements":"Met requirements", "avatar_url":"Picture",
            "track":"Track",
        })
        if "Picture" not in classified:
            classified["Picture"] = ""
        if selected_month == current_month:
            st.info("Current-month eligibility uses the latest complete daily Creator Data read through yesterday. It updates once each morning.")
    elif selected_month == current_month:
        st.info("Waiting for the first complete daily Creator Data read; current progress is temporarily using Goal data.")

    c1, c2 = st.columns([1, 1.5])
    manager = c1.selectbox("Manager", ["All managers", *manager_names], key="mission_reward_manager")
    search = c2.text_input("Search creators", key="mission_reward_search").strip()
    if manager != "All managers":
        classified = classified[classified["Manager"] == manager]
    if search:
        classified = classified[classified["Creator"].str.contains(re.escape(search), case=False, na=False)]

    qualified = classified[classified["Eligible"]].copy()
    if "Track" not in classified:
        classified["Track"] = classified["Milestone"].fillna(0).gt(0)
    if "Met requirements" not in classified:
        classified["Met requirements"] = ""
    if "Missing requirements" not in classified:
        classified["Missing requirements"] = classified.get("Missing requirement", "")
    if live_progress and not using_creator_data:
        classified = classified[classified["Diamonds"] >= MILESTONES[-1][0]].copy()
    near = classified[classified["Track"] & ~classified["Eligible"]].copy()
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
        _display_table(near[["Picture","Creator","Manager","Diamonds","Valid LIVE days","Valid LIVE hours","Maintained or ranked up","Reward","Milestone","Prize value","Met requirements","Missing requirements"]], min(760, 88 + len(near) * 48))

    _event_section(engine, classified, manager_names, selected_month)


def _render_monthly_prizes(engine, creators: pd.DataFrame, manager_names: list[str]) -> None:
    st.subheader("Monthly Creator Prizes")
    st.caption("Track entries for each monthly drawing. Creator pictures and progress update from the daily Creator Data read.")
    rows = _classify(creators)
    current_month = dt.datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m")
    snapshots = pd.read_sql(text("SELECT * FROM monthly_reward_results WHERE month_key=:month_key"), engine, params={"month_key": current_month})
    if not snapshots.empty:
        rows = snapshots.rename(columns={
            "creator_id":"Creator ID", "username":"Creator", "manager_name":"Manager",
            "diamonds":"Diamonds", "valid_live_days":"Valid LIVE days",
            "valid_live_hours":"Valid LIVE hours", "avatar_url":"Picture",
        })
        st.info("Using the latest complete daily Creator Data snapshot through yesterday.")
    else:
        st.info("Waiting for the first daily Creator Data snapshot; showing available Goal data for now.")
    for column, default in (("Picture", ""), ("Manager", "Unassigned"), ("Creator", ""), ("Diamonds", 0), ("Valid LIVE days", 0), ("Valid LIVE hours", 0)):
        if column not in rows:
            rows[column] = default
    c1, c2 = st.columns([1, 1.5])
    manager = c1.selectbox("Monthly prizes manager", ["All managers", *manager_names], key="monthly_prizes_manager")
    search = c2.text_input("Search monthly prize creators", key="monthly_prizes_search").strip()
    if manager != "All managers":
        rows = rows[rows["Manager"] == manager]
    if search:
        rows = rows[rows["Creator"].astype(str).str.contains(re.escape(search), case=False, na=False)]

    drawing_one = rows[(pd.to_numeric(rows["Valid LIVE days"], errors="coerce").fillna(0) >= 8) & (pd.to_numeric(rows["Valid LIVE hours"], errors="coerce").fillna(0) >= 20)].copy()
    drawing_two = rows[(pd.to_numeric(rows["Valid LIVE days"], errors="coerce").fillna(0) >= 15) & (pd.to_numeric(rows["Valid LIVE hours"], errors="coerce").fillna(0) >= 40) & (pd.to_numeric(rows["Diamonds"], errors="coerce").fillna(0) >= 100)].copy()
    drawing_three = rows[pd.to_numeric(rows["Diamonds"], errors="coerce").fillna(0) >= 200_000].copy()

    m1, m2, m3 = st.columns(3)
    m1.metric("Drawing 1 entries", f"{len(drawing_one):,}", help="8 valid days and 20 LIVE hours by the 20th")
    m2.metric("Drawing 2 entries", f"{len(drawing_two):,}", help="Level 3: 15 valid days, 40 LIVE hours, and at least 100 diamonds")
    m3.metric("Drawing 3 entries", f"{len(drawing_three):,}", help="At least 200,000 diamonds during the month")

    prize_columns = ["Picture", "Creator", "Manager", "Diamonds", "Valid LIVE days", "Valid LIVE hours"]
    with st.expander("Drawing 1 — 8 days and 20 hours by the 20th", expanded=True):
        if drawing_one.empty:
            st.info("No creators currently qualify for Drawing 1.")
        else:
            _display_table(drawing_one[prize_columns].sort_values(["Valid LIVE days", "Valid LIVE hours", "Creator"], ascending=[False, False, True]), min(640, 88 + len(drawing_one) * 44))
    with st.expander("Drawing 2 — Reach Level 3", expanded=True):
        if drawing_two.empty:
            st.info("No creators currently qualify for Drawing 2.")
        else:
            _display_table(drawing_two[prize_columns].sort_values(["Diamonds", "Creator"], ascending=[False, True]), min(640, 88 + len(drawing_two) * 44))
    with st.expander("Drawing 3 — Reach 200,000 diamonds", expanded=True):
        if drawing_three.empty:
            st.info("No creators currently qualify for Drawing 3.")
        else:
            _display_table(drawing_three[prize_columns].sort_values(["Diamonds", "Creator"], ascending=[False, True]), min(640, 88 + len(drawing_three) * 44))


def render_monthly_mission_rewards(engine, creators: pd.DataFrame, manager_names: list[str]) -> None:
    mission_tab, prizes_tab = st.tabs(["Mission Rewards", "Monthly Prizes"])
    with mission_tab:
        _render_mission_rewards(engine, creators, manager_names)
    with prizes_tab:
        _render_monthly_prizes(engine, creators, manager_names)
