def render_monthly_rewards(st, pd, re, creators, manager_names, numeric_series, manager_series, render_read_table):
    st.subheader("Monthly Creator Rewards")
    st.caption(
        "Creators receive only their single highest completed milestone. "
        "Every milestone requires the listed diamonds, valid LIVE days, and valid LIVE hours."
    )

    milestones = [
        {"diamonds": 5_000_000, "days": 20, "hours": 30, "reward": "TikTok Universe", "value": 44_999},
        {"diamonds": 2_000_000, "days": 20, "hours": 30, "reward": "TikTok Stars", "value": 39_999},
        {"diamonds": 1_500_000, "days": 20, "hours": 30, "reward": "Dragon Flame", "value": 26_999},
        {"diamonds": 1_000_000, "days": 20, "hours": 30, "reward": "Adam's Dream", "value": 25_999},
        {"diamonds": 500_000, "days": 10, "hours": 20, "reward": "Interstellar", "value": 10_000},
        {"diamonds": 300_000, "days": 10, "hours": 20, "reward": "Leon the Kitten", "value": 4_888},
        {"diamonds": 150_000, "days": 10, "hours": 20, "reward": "Motorcycle", "value": 2_988},
    ]

    rows = creators.copy()
    if not rows.empty:
        rows["_diamonds"] = numeric_series(rows, "diamonds")
        rows["_days"] = numeric_series(rows, "valid_live_days")
        rows["_hours"] = numeric_series(rows, "valid_live_hours")
        rows["_creator"] = rows.get(
            "username", rows.get("creator_id", pd.Series("", index=rows.index))
        ).fillna("").astype(str)
        rows["_manager"] = manager_series(rows)
        photo_candidates = [
            "avatar_url", "profile_image_url", "profile_photo_url", "photo_url",
            "image_url", "avatar", "profile_picture",
        ]
        photo_column = next((column for column in photo_candidates if column in rows.columns), None)
        rows["_photo"] = rows[photo_column].fillna("").astype(str) if photo_column else ""

        def highest_reward(row):
            for milestone in milestones:
                if (
                    row["_diamonds"] >= milestone["diamonds"]
                    and row["_days"] >= milestone["days"]
                    and row["_hours"] >= milestone["hours"]
                ):
                    return pd.Series(milestone)
            return pd.Series({"diamonds": 0, "days": 0, "hours": 0, "reward": "", "value": 0})

        matches = rows.apply(highest_reward, axis=1)
        rows = pd.concat(
            [rows.reset_index(drop=True), matches.reset_index(drop=True).add_prefix("_reward_")], axis=1
        )
        rows = rows[rows["_reward_value"] > 0].copy()

    left, right = st.columns([1, 1.4])
    with left:
        manager = st.selectbox("Reward manager", ["All managers", *manager_names], key="reward_manager_filter")
    with right:
        search = st.text_input("Search qualifying creators", key="reward_creator_search").strip().casefold()

    filtered = rows.copy()
    if not filtered.empty and manager != "All managers":
        filtered = filtered[filtered["_manager"] == manager].copy()
    if not filtered.empty and search:
        filtered = filtered[
            filtered["_creator"].str.casefold().str.contains(re.escape(search), na=False)
        ].copy()

    total_value = int(filtered["_reward_value"].sum()) if not filtered.empty else 0
    summary_1, summary_2, summary_3 = st.columns(3)
    summary_1.metric("Qualifying creators", f"{len(filtered):,}")
    summary_2.metric("Total prize value", f"{total_value:,} coins")
    summary_3.metric(
        "Highest milestone reached",
        f"{int(filtered['_reward_diamonds'].max()):,} diamonds" if not filtered.empty else "None yet",
    )

    with st.expander("Reward milestone requirements"):
        milestone_display = pd.DataFrame([
            {
                "Diamond milestone": f"{item['diamonds']:,}",
                "Valid LIVE days": item["days"],
                "Valid LIVE hours": item["hours"],
                "Reward": item["reward"],
                "Prize value": f"{item['value']:,} coins",
            }
            for item in reversed(milestones)
        ])
        render_read_table(milestone_display, height=320)

    if filtered.empty:
        st.info("No creators currently meet both the diamond and LIVE-time requirements for a monthly reward.")
        return

    filtered = filtered.sort_values(
        ["_reward_diamonds", "_diamonds", "_creator"], ascending=[False, False, True]
    )
    display = pd.DataFrame({
        "Picture": filtered["_photo"],
        "Creator": filtered["_creator"],
        "Manager": filtered["_manager"],
        "Monthly diamonds": filtered["_diamonds"].astype("int64"),
        "Valid LIVE days": filtered["_days"].astype("int64"),
        "Valid LIVE hours": filtered["_hours"].round(1),
        "Highest reward earned": filtered["_reward_reward"],
        "Qualified milestone": filtered["_reward_diamonds"].astype("int64"),
        "Prize value": filtered["_reward_value"].astype("int64"),
    })
    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        height=min(760, 72 + (len(display) * 56)),
        column_config={
            "Picture": st.column_config.ImageColumn("Picture", width="small"),
            "Monthly diamonds": st.column_config.NumberColumn(format="%,d"),
            "Qualified milestone": st.column_config.NumberColumn(format="%,d diamonds"),
            "Prize value": st.column_config.NumberColumn(format="%,d coins"),
        },
    )
