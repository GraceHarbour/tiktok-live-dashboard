#!/usr/bin/env python3
import json
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

SCHEDULE_CHANNEL = "1543825908039680050"
CONFIRMATION_CHANNEL = "1521966524322156749"
TOKEN_FILE = Path.home() / ".config/creator-reader/discord-bot-token"
ET = ZoneInfo("America/New_York")


def fetch(channel):
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip() or TOKEN_FILE.read_text().splitlines()[0].strip()
    request = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel}/messages?limit=100",
        headers={"Authorization": f"Bot {token}", "User-Agent": "GraceHarbourBattleReader/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def mention_names(message):
    names = {}
    for user in message.get("mentions", []):
        member = user.get("member") or {}
        names[str(user.get("id"))] = member.get("nick") or user.get("global_name") or user.get("username") or ""
    return names


def message_text(message):
    parts = [message.get("content", "")]
    for embed in message.get("embeds", []):
        parts.extend([embed.get("title", ""), embed.get("description", "")])
        for field in embed.get("fields", []):
            parts.extend([field.get("name", ""), field.get("value", "")])
    return "\n".join(str(part) for part in parts if part)


def parse_date(text, reference):
    match = re.search(r"(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)?day\s+([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?|(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)?\s*([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?", text, re.I)
    if not match:
        return None
    month_name, day = (match.group(1), match.group(2)) if match.group(1) else (match.group(3), match.group(4))
    try:
        month = datetime.strptime(month_name[:3], "%b").month
    except ValueError:
        return None
    result = datetime(reference.year, month, int(day), tzinfo=ET)
    if result < reference - timedelta(days=180):
        result = result.replace(year=reference.year + 1)
    return result.date()


def parse_time(text):
    match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\s*(?:E[SD]T|ET)", text, re.I)
    if not match:
        return None
    hour = int(match.group(1)) % 12 + (12 if match.group(3).upper() == "PM" else 0)
    return hour, int(match.group(2) or 0)


def entry_blocks(text, reference):
    blocks, current_day_text, current = [], "", []
    for line in text.splitlines():
        if parse_date(line, reference):
            current_day_text = line
        if parse_time(line):
            if current:
                blocks.append("\n".join(current))
            current = ([current_day_text] if current_day_text and current_day_text != line else []) + [line]
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def details(block):
    opponent = "pending"
    match = re.search(r"\bvs\.?\s+(.+?)(?:\s*\||$)", block, re.I)
    if match:
        opponent = re.sub(r"<@!?\d+>", "", match.group(1)).strip(" .|*") or "pending"
    diamond = re.search(r"💎\s*([\d.]+\s*[kKmM]?(?:\s*[-–]\s*[\d.]+\s*[kKmM]?)?)", block)
    upper = block.upper()
    power = "Power-ups not allowed" if "NO POWER" in upper else ("Power-ups allowed" if "POWER UP" in upper else "Rule pending")
    adult = "18+ off" if "18+ OFF" in upper else ("18+ on" if "18+ ON" in upper else "18+ not provided")
    battle_format = "2v2" if "2 V 2" in upper or "2V2" in upper else "Single"
    return opponent, diamond.group(1).replace(" ", "") if diamond else "", power, adult, battle_format


def confirmed_records(messages):
    records = []
    now = datetime.now(ET)
    for message in messages:
        names = mention_names(message)
        for block in entry_blocks(message_text(message), now):
            day, clock = parse_date(block, now), parse_time(block)
            ids = re.findall(r"<@!?(\d+)>", block)
            if not day or not clock or not ids:
                continue
            creator_id = ids[0]
            opponent, diamonds, power, adult, battle_format = details(block)
            records.append({"day": day, "clock": clock, "discord_id": creator_id,
                            "creator": names.get(creator_id, creator_id), "opponent": opponent,
                            "diamonds": diamonds, "power": power, "adult": adult,
                            "format": battle_format, "confirmed": opponent.casefold() != "pending"})
    return records


def scheduled_records(messages):
    records = []
    now = datetime.now(ET)
    for message in messages:
        names = mention_names(message)
        for block in entry_blocks(message_text(message), now):
            day, clock = parse_date(block, now), parse_time(block)
            ids = re.findall(r"<@!?(\d+)>", block)
            if not day or not clock or not ids:
                continue
            creator_id = ids[0]
            opponent, diamonds, power, adult, battle_format = details(block)
            records.append({"day": day, "clock": clock, "discord_id": creator_id,
                            "creator": names.get(creator_id, creator_id), "opponent": opponent,
                            "diamonds": diamonds, "power": power, "adult": adult,
                            "format": battle_format, "confirmed": False})
    return records


def merged_records(scheduled, confirmed):
    merged = []
    def tokens(record):
        vals = {normalized(record.get("creator", "")), normalized(record.get("opponent", ""))}
        return {v for v in vals if v and v != "pending"}
    for record in scheduled + confirmed:
        identity = tokens(record)
        match = None
        for existing in merged:
            if existing["day"] != record["day"] or existing["clock"] != record["clock"]:
                continue
            overlap = identity & tokens(existing)
            if overlap:
                match = existing
                break
        if match is None:
            merged.append(record.copy())
            continue
        for field, value in record.items():
            if value not in (None, "", "pending", "Rule pending", "18+ not provided"):
                match[field] = value
    return merged

def creator_match(name, creators):
    target = normalized(name)
    if not target:
        return None
    for row in creators:
        if target in {normalized(row.get("tiktok_username", "")), normalized(row.get("display_name", ""))}:
            return row
    return None


def store(records):
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgresql+psycopg://"):
        url = "postgresql://" + url.removeprefix("postgresql+psycopg://")
    inserted = updated = 0
    with psycopg.connect(url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, display_name, tiktok_username, manager_name FROM creators")
            creators = [
                {"id": row[0], "display_name": row[1], "tiktok_username": row[2], "manager_name": row[3]}
                for row in cursor.fetchall()
            ]
            for record in records:
                start = datetime.combine(record["day"], datetime.min.time(), ET).replace(
                    hour=record["clock"][0], minute=record["clock"][1]).astimezone(timezone.utc)
                if start < datetime.now(timezone.utc) - timedelta(hours=1):
                    continue
                end = start + timedelta(minutes=30)
                event_id = f"discord-{record['day'].strftime('%Y%m%d')}-{record['clock'][0]:02d}{record['clock'][1]:02d}-{record['discord_id']}"
                creator = creator_match(record["creator"], creators)
                # Reuse a manually scheduled event at the same instant when it
                # already names this creator; confirmations must update it.
                creator_token = normalized(record["creator"])
                cursor.execute("""SELECT e.event_id,e.event_name,p.username
                    FROM community_events e LEFT JOIN community_event_participants p ON p.event_id=e.event_id
                    WHERE e.start_at=%s""", (start.isoformat(),))
                for existing_id, existing_name, participant_username in cursor.fetchall():
                    existing_tokens = normalized(existing_name) + " " + normalized(participant_username or "")
                    creator_username = normalized(creator.get("tiktok_username", "")) if creator else ""
                    opponent_token = normalized(record.get("opponent", ""))
                    same_opponent = opponent_token and opponent_token != "pending" and opponent_token in existing_tokens
                    if same_opponent or (creator_token and creator_token in existing_tokens) or (creator_username and creator_username in existing_tokens):
                        event_id = existing_id
                        break
                title = f"[BATTLE] {record['creator']} vs {record['opponent']}"
                extra = " · ".join(value for value in [record["diamonds"], record["power"], record["adult"], record["format"]] if value)
                title = f"{title} · {extra}"
                cursor.execute("SELECT 1 FROM community_events WHERE event_id=%s", (event_id,))
                exists = cursor.fetchone() is not None
                cursor.execute("""INSERT INTO community_events(event_id,event_name,start_at,end_at,status,created_at)
                    VALUES(%s,%s,%s,%s,'scheduled',%s)
                    ON CONFLICT(event_id) DO UPDATE SET event_name=EXCLUDED.event_name,start_at=EXCLUDED.start_at,end_at=EXCLUDED.end_at""",
                    (event_id, title, start.isoformat(), end.isoformat(), datetime.now(timezone.utc).isoformat()))
                inserted += int(not exists)
                updated += int(exists)
                if creator is not None:
                    cursor.execute("DELETE FROM community_event_participants WHERE event_id=%s", (event_id,))
                    cursor.execute("""INSERT INTO community_event_participants(event_id,creator_id,username,manager,added_at)
                        VALUES(%s,%s,%s,%s,%s) ON CONFLICT(event_id,creator_id) DO UPDATE SET username=EXCLUDED.username,manager=EXCLUDED.manager""",
                        (event_id, str(creator.get("id", "")), str(creator.get("tiktok_username", "")),
                         str(creator.get("manager_name", "")), datetime.now(timezone.utc).isoformat()))

            # Remove stale reader-created duplicates at the same start time.
            # Manual events and past events are never deleted.
            cursor.execute("SELECT event_id,event_name,start_at FROM community_events WHERE event_id LIKE 'discord-%' AND start_at >= %s ORDER BY start_at,event_id", (datetime.now(timezone.utc) - timedelta(hours=1),))
            rows = cursor.fetchall()
            grouped = {}
            for eid, ename, estart in rows:
                grouped.setdefault(estart, []).append((eid, ename or ""))
            for same_time in grouped.values():
                if len(same_time) < 2:
                    continue
                kept = []
                for eid, ename in same_time:
                    tokens = {t for t in normalized(ename).split() if t not in {"pending", "open", "vs", "battle"}}
                    duplicate = None
                    for kid, kname, ktokens in kept:
                        if tokens & ktokens:
                            score = int("pending" not in normalized(ename)) + int("[open]" not in normalized(ename))
                            kscore = int("pending" not in normalized(kname)) + int("[open]" not in normalized(kname))
                            if score > kscore:
                                cursor.execute("DELETE FROM community_event_participants WHERE event_id=%s", (kid,))
                                cursor.execute("DELETE FROM community_events WHERE event_id=%s", (kid,))
                                kept.remove((kid, kname, ktokens))
                            else:
                                duplicate = eid
                            break
                           if duplicate:
                           cursor.execute("DELETE FROM community_event_participants WHERE event_id=%s", (duplicate,))
                          cursor.execute("DELETE FROM community_events WHERE event_id=%s", (duplicate,))
                    else:
                        kept.append((eid, ename, tokens))
    print(f"Discord battle sync: {inserted} inserted, {updated} updated, {len(records)} unique source battles")


def main():
    scheduled = fetch(SCHEDULE_CHANNEL)
    confirmed = fetch(CONFIRMATION_CHANNEL)
    all_messages = scheduled + confirmed
    store(merged_records(scheduled_records(all_messages), confirmed_records(all_messages)))


if __name__ == "__main__":
    main()
