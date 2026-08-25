"""Local-only dashboard for authorized Creator Network Backstage data."""

from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .database import CreatorNetworkDatabase
from .security import require_access_admin, require_dashboard_user, require_sync_worker
from .tiers import compare_months, diamond_amount


app = FastAPI(title="Creator Network Data Hub")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


def database() -> CreatorNetworkDatabase:
    db = CreatorNetworkDatabase(Path(os.environ.get("CREATOR_DATABASE", "data/creator_network.sqlite3")))
    db.ensure_initial_owner(os.environ.get("CREATOR_INITIAL_OWNER_EMAIL"))
    return db


def page(title: str, body: str) -> str:
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>
    <style>
    :root{{--ink:#11243b;--muted:#617086;--teal:#06b6c7;--navy:#152b4f;--gold:#ffb84d;--surface:#fff;--line:#e8eef5}}
    *{{box-sizing:border-box}}body{{font-family:Inter,ui-sans-serif,system-ui,sans-serif;max-width:1240px;margin:0 auto;padding:28px 22px 60px;color:var(--ink);background:linear-gradient(135deg,#f4fbff,#f8f7ff 55%,#fffaf2)}}
    nav{{display:flex;align-items:center;gap:10px;margin-bottom:26px;padding:8px 14px;background:rgba(255,255,255,.78);border:1px solid var(--line);border-radius:16px;box-shadow:0 8px 26px #1834560c}}nav a{{color:var(--navy);font-weight:700;text-decoration:none;padding:8px 10px;border-radius:9px}}nav a:hover{{background:#e8f9fb}}.brand{{display:flex;align-items:center;gap:10px;margin-right:auto;padding:2px 8px 2px 0!important;line-height:1.05!important}}.brand:hover{{background:transparent!important}}.brand img{{width:52px;height:52px;object-fit:cover;border-radius:50%;box-shadow:0 4px 12px #112b4a28}}.brand-title{{font-size:.86rem;letter-spacing:.05em;text-transform:uppercase}}.brand-title small{{display:block;margin-top:4px;color:#087b91;font-size:.65rem;letter-spacing:.12em}}.navlinks{{display:flex;gap:4px;align-items:center}}
    h1{{font-size:clamp(1.7rem,3vw,2.45rem);letter-spacing:-.04em;margin:0 0 20px}}h2{{margin-top:32px;font-size:1.12rem}}.hero{{display:grid;grid-template-columns:1.5fr .9fr;gap:18px;padding:30px;border-radius:24px;color:#fff;background:radial-gradient(circle at 82% 16%,#22d5da55,transparent 28%),linear-gradient(135deg,#112b4a,#195c77 64%,#087b91);box-shadow:0 20px 40px #112b4a2b;margin-bottom:22px}}.eyebrow{{font-size:.73rem;text-transform:uppercase;letter-spacing:.14em;color:#94eff2;font-weight:800}}.hero h1{{margin:9px 0 10px}}.hero p{{max-width:590px;margin:0;color:#e1f7f8;line-height:1.55}}.month-pill{{align-self:center;justify-self:end;background:#ffffff18;border:1px solid #ffffff33;border-radius:18px;padding:16px 18px;min-width:175px}}.month-pill strong{{display:block;font-size:1.25rem;margin-top:4px}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:16px}}.card{{position:relative;overflow:hidden;background:linear-gradient(140deg,var(--navy),#245f83);color:#fff;padding:22px;border-radius:18px;min-height:112px;box-shadow:0 16px 30px #112b4a21}}.card:nth-child(2){{background:linear-gradient(140deg,#007f91,#09c5ce)}}.card:nth-child(3){{background:linear-gradient(140deg,#9a5a16,#f2a333)}}.card strong{{display:block;font-size:2rem;letter-spacing:-.06em;margin-bottom:3px}}.section-grid{{display:grid;grid-template-columns:1.15fr .85fr;gap:18px;margin-top:22px}}.panel{{background:#ffffffcf;border:1px solid var(--line);border-radius:18px;padding:20px;box-shadow:0 12px 28px #1834560b}}.panel h2{{margin:0 0 8px}}.empty{{padding:22px 4px 8px;color:var(--muted);line-height:1.55}}.quick-links{{display:flex;gap:10px;flex-wrap:wrap;margin-top:17px}}.quick-links a{{background:#e9fbfc;color:#087b91;text-decoration:none;font-weight:800;padding:10px 13px;border-radius:10px}}.status-dot{{display:inline-block;width:9px;height:9px;background:#36d29a;border-radius:50%;margin-right:7px;box-shadow:0 0 0 4px #36d29a22}}
    table{{border-collapse:separate;border-spacing:0;width:100%;margin-top:14px;background:rgba(255,255,255,.9);border:1px solid var(--line);border-radius:15px;overflow:hidden;box-shadow:0 10px 26px #1834560b}}th,td{{padding:13px 14px;border-bottom:1px solid var(--line);text-align:left}}th{{font-size:.74rem;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);background:#f7fbfd}}tr:last-child td{{border-bottom:0}}tr:hover td{{background:#f3fcfd}}td a{{color:#007d91;font-weight:700;text-decoration:none}}pre{{white-space:pre-wrap;background:#10263f;color:#eaf7ff;padding:16px;border-radius:12px}}form{{margin:14px 0}}input,select,button{{font:inherit;padding:9px 11px;margin-right:6px;border-radius:9px;border:1px solid #ccd9e8}}button{{background:var(--teal);color:#fff;border:0;font-weight:800;cursor:pointer}}.active{{color:#087b58;font-weight:700}}.inactive{{color:#ad3b28;font-weight:700}}
    .tabs{{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0 20px}}.tabs a{{text-decoration:none;color:var(--navy);font-weight:800;padding:9px 12px;border:1px solid var(--line);border-radius:999px;background:#fff}}.tabs a:hover,.tabs a.current{{color:#fff;background:#087b91;border-color:#087b91}}.metrics{{display:grid;grid-template-columns:repeat(4,minmax(130px,1fr));gap:12px;margin:18px 0}}.metric{{background:#f7fbfd;border:1px solid var(--line);border-radius:14px;padding:14px}}.metric strong{{display:block;font-size:1.55rem;letter-spacing:-.04em;color:#112b4a}}.metric span{{display:block;font-size:.76rem;color:var(--muted);font-weight:700;margin-top:4px}}.creator-table td{{vertical-align:middle}}.creator-name{{font-weight:800;color:#112b4a}}.subtle{{color:var(--muted)}}
    @media(max-width:760px){{body{{padding:16px 12px 42px}}nav{{align-items:flex-start;flex-wrap:wrap}}.brand{{width:100%}}.navlinks{{width:100%;overflow:auto}}.brand img{{width:44px;height:44px}}.hero,.section-grid{{grid-template-columns:1fr}}.month-pill{{justify-self:start}}.metrics{{grid-template-columns:repeat(2,minmax(120px,1fr))}}}}
    </style>
    </head><body><nav><a class='brand' href='/dashboard'><img src='/static/grace-harbour-creator-network.jpg' alt='Grace Harbour Media Creator Network logo'><span class='brand-title'>Grace Harbour Media<small>Creator Network</small></span></a><div class='navlinks'><a href='/dashboard'>Dashboard</a><a href='/applications'>New applications</a><a href='/access'>Access management</a></div></nav><h1>{html.escape(title)}</h1>{body}</body></html>"""


@app.get("/health")
async def health() -> dict[str, str]:
    """Unauthenticated health check; it does not expose Creator Network data."""
    return {"status": "ok"}


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_notice() -> str:
    """Public notice required for the Google sign-in consent screen."""
    return page(
        "Privacy Notice",
        """<p><strong>Effective date: August 23, 2026</strong></p>
        <p>Grace Harbour Creator Network Dashboard is a private business dashboard for authorized Grace Harbour Media team members.</p>
        <h2>Information we use</h2><p>We use the Google account email address provided at sign-in to control access. The dashboard can display Creator Network information that authorized team members choose to import from approved business systems.</p>
        <h2>How we use it</h2><p>Information is used only to operate the dashboard, manage team access, and support Creator Network reporting. We do not sell personal information or use it for advertising.</p>
        <h2>Access and security</h2><p>Access is limited to people approved by Grace Harbour Media. Team members may request access changes through a dashboard administrator.</p>
        <h2>Questions</h2><p>For privacy questions or account removal requests, contact your Grace Harbour Media dashboard administrator.</p>""",
    )


@app.get("/terms", response_class=HTMLResponse)
async def terms_of_use() -> str:
    """Public terms required for the Google sign-in consent screen."""
    return page(
        "Terms of Use",
        """<p><strong>Effective date: August 23, 2026</strong></p>
        <p>This dashboard is for authorized Grace Harbour Media Creator Network team members only.</p>
        <h2>Authorized use</h2><p>Use the dashboard only for legitimate Creator Network work and only with the access level assigned to you. Do not share your sign-in session, export information outside approved business processes, or attempt to access data outside your role.</p>
        <h2>Data accuracy</h2><p>Dashboard information is a reporting aid. Confirm important business decisions against the authorized source system.</p>
        <h2>Access removal</h2><p>Grace Harbour Media may change or remove access when needed to protect the dashboard and its information.</p>
        <h2>Questions</h2><p>Contact your Grace Harbour Media dashboard administrator with questions about these terms.</p>""",
    )


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> str:
    db = database()
    require_dashboard_user(request, db)
    data = db.dashboard()
    refresh = db.refresh_status()
    snapshot = data["manager_goals"]
    creator_snapshot = db.latest_snapshot("goals", "creators")
    previous_creator_snapshot = db.previous_snapshot(
        "goals", "creators", str(creator_snapshot.get("month") or "")
    ) if creator_snapshot else None
    creator_data = creator_goal_data(creator_snapshot, previous_creator_snapshot)
    goal_headers = snapshot.get("headers", []) if snapshot else []
    goal_rows = snapshot.get("rows", []) if snapshot else []
    if not isinstance(goal_headers, list):
        goal_headers = []
    if not isinstance(goal_rows, list):
        goal_rows = []
    latest_goal_month = str(snapshot.get("month") or "Waiting") if snapshot else "Waiting"
    verified_creators = creator_data["rows"] if creator_data else []
    manager_names = creator_data["managers"] if creator_data else []
    new_creator_total = new_creator_count(snapshot)
    cards = "".join((
        f"<div class='card'><strong>{len(manager_names) or len(goal_rows)}</strong><br>Managers</div>",
        f"<div class='card'><strong>{new_creator_total if new_creator_total is not None else '—'}</strong><br>New creators this month</div>",
        f"<div class='card'><strong>{len(verified_creators)}</strong><br>Creators in latest view</div>",
        f"<div class='card'><strong>{html.escape(latest_goal_month)}</strong><br>Current goal month</div>",
    ))
    captures = "".join(f"<tr><td>{html.escape(row['source'])}</td><td>{html.escape(row['view_name'] or '')}</td><td>{html.escape(row['month'] or '')}</td><td>{html.escape(row['captured_at'])}</td></tr>" for row in data["snapshots"]) or "<tr><td colspan='4'>No Backstage snapshots have been synced yet.</td></tr>"
    latest_month = data["snapshots"][0]["month"] if data["snapshots"] else "Waiting for first sync"
    manager_tabs = "".join(
        f"<a href='/goals/managers?manager={quote(name)}'>{html.escape(name)}</a>"
        for name in manager_names
    ) or "<span class='subtle'>Manager tabs will appear after the verified Creator capture completes.</span>"
    refresh_message = "A refresh is waiting for the secure reader." if refresh["requested"] else "Updates every 15 minutes while the secure reader is running."
    return page("Creator Network Dashboard", f"""
    <section class='hero'><div><div class='eyebrow'><span class='status-dot'></span>Private team workspace</div><h1>Creator Network Hub</h1><p>Current Creator-view goals are listed under each manager. Only the manager name and verified Creator fields are shown.</p><div class='quick-links'><a href='/goals/managers'>Managers</a><a href='/applications'>New applications</a></div></div><aside class='month-pill'><span class='eyebrow'>Current reporting month</span><strong>{html.escape(str(latest_month))}</strong><small>Latest authorized Backstage capture</small></aside></section>
    <div class='cards'>{cards}</div>
    <section class='panel'><h2>Managers</h2><div class='tabs'><a href='/goals/managers'>All managers</a>{manager_tabs}</div><form method='post' action='/refresh'><button type='submit'>Refresh from Backstage</button></form><small>{html.escape(refresh_message)}</small></section>
    <section class='section-grid'><div class='panel'><h2>Latest Backstage updates</h2><table><tr><th>Source</th><th>View</th><th>Month</th><th>Captured</th></tr>{captures}</table></div><aside class='panel'><h2>Planned data areas</h2><div class='empty'>Creator profile photos, Business Essentials, and New Applications will be added only after their authorized Backstage views are captured and verified.</div></aside></section>""")


@app.get("/applications", response_class=HTMLResponse)
async def applications(request: Request) -> str:
    db = database()
    require_dashboard_user(request, db)
    rows = db.connection.execute("""SELECT * FROM applications ORDER BY application_date DESC, id DESC LIMIT 500""").fetchall()
    body_rows = "".join(
        f"<tr><td>{html.escape(row['handle'] or row['creator_name'] or '')}</td><td>{html.escape(row['manager_name'] or '')}</td>"
        f"<td>{html.escape(row['application_date'] or '')}</td><td>{html.escape(row['invite_expires'] or '')}</td>"
        f"<td>{html.escape(row['invite_sent'] or '')}</td><td>{html.escape(row['invite_accepted'] or '')}</td><td>{html.escape(row['application_status'] or '')}</td></tr>"
        for row in rows
    ) or "<tr><td colspan='7'>No applications have been synced yet.</td></tr>"
    return page("New Applications", f"<p>New applicants and invite progress from authorized Scout Creator data.</p><table><tr><th>Creator</th><th>Applied under</th><th>Applied</th><th>Invite expires</th><th>Invite sent</th><th>Accepted</th><th>Status</th></tr>{body_rows}</table>")


@app.post("/refresh")
async def request_backstage_refresh(request: Request) -> RedirectResponse:
    """Queue a read-only refresh for the separate, authorized Backstage worker."""
    db = database()
    user = require_dashboard_user(request, db)
    db.request_refresh(str(user["email"]))
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/internal/backstage/snapshot")
async def receive_backstage_snapshot(request: Request) -> dict[str, object]:
    """Receive a signed snapshot from the isolated reader, never a web-browser session."""
    raw_body = await request.body()
    require_sync_worker(
        timestamp=request.headers.get("X-Creator-Timestamp"),
        signature=request.headers.get("X-Creator-Signature"),
        body=raw_body,
    )
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as error:
        raise HTTPException(400, "The sync payload must be valid JSON.") from error
    if not isinstance(payload, dict):
        raise HTTPException(400, "The sync payload must be an object.")
    saved = database().import_snapshot_payload(payload)
    return {"saved": True, "source": saved["source"], "captured_at": saved["captured_at"]}


def first_line(value: object) -> str:
    """Keep the readable label and remove Backstage's secondary profile lines."""
    for line in str(value or "").splitlines():
        clean = line.strip()
        if clean and clean.lower() not in {"/not set", "not allocated"}:
            return clean
    return ""


def header_index(headers: list[object], *names: str) -> int | None:
    normalized = {str(header).strip().casefold(): index for index, header in enumerate(headers)}
    for name in names:
        if name.casefold() in normalized:
            return normalized[name.casefold()]
    return None


def number_in_cell(value: object) -> int | None:
    """Read Backstage's displayed current value, before a target such as ``/ Not set``."""
    text = first_line(value).replace(",", "")
    match = re.search(r"\d+(?:\.\d+)?\s*[KM]?", text, flags=re.IGNORECASE)
    return diamond_amount(match.group(0).replace(" ", "")) if match else None


def new_creator_count(snapshot: dict[str, object] | None) -> int | None:
    """Sum the current-month New creators values from the verified manager goals view."""
    if not snapshot:
        return None
    headers = snapshot.get("headers", [])
    rows = snapshot.get("rows", [])
    if not isinstance(headers, list) or not isinstance(rows, list):
        return None
    index = header_index(headers, "New creators", "New creator")
    if index is None:
        return None
    values = [
        number_in_cell(row[index])
        for row in rows
        if isinstance(row, list) and len(row) > index and number_in_cell(row[index]) is not None
    ]
    return sum(values) if values else None


def creator_goal_data(
    snapshot: dict[str, object] | None,
    previous: dict[str, object] | None,
) -> dict[str, object] | None:
    """Normalize a Creator-tab capture without showing IDs, emails, or hidden Manager rows."""
    if not snapshot:
        return None
    headers = snapshot.get("headers", [])
    raw_rows = snapshot.get("rows", [])
    if not isinstance(headers, list) or not isinstance(raw_rows, list):
        return None
    creator_index = header_index(headers, "Creator")
    if creator_index is None:
        return None

    indexes = {
        "creator": creator_index,
        "manager": header_index(headers, "Manager", "Manager name"),
        "diamonds": header_index(headers, "Diamonds"),
        "live_days": header_index(headers, "Valid go LIVE days", "Valid live days"),
        "live_duration": header_index(headers, "Valid LIVE duration", "Valid live duration"),
        "bonus": header_index(headers, "Bonus contribution"),
        "tier": header_index(headers, "Tier"),
        "activeness": header_index(headers, "Activeness"),
    }

    previous_by_creator: dict[str, list[object]] = {}
    if previous:
        previous_headers = previous.get("headers", [])
        previous_rows = previous.get("rows", [])
        if isinstance(previous_headers, list) and isinstance(previous_rows, list):
            prior_creator_index = header_index(previous_headers, "Creator")
            if prior_creator_index is not None:
                previous_by_creator = {
                    first_line(row[prior_creator_index]).casefold(): row
                    for row in previous_rows
                    if isinstance(row, list) and len(row) > prior_creator_index and first_line(row[prior_creator_index])
                }
            prior_diamond_index = header_index(previous_headers, "Diamonds")
        else:
            prior_diamond_index = None
    else:
        prior_diamond_index = None

    rows: list[dict[str, object]] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, list) or len(raw_row) <= creator_index:
            continue
        creator = first_line(raw_row[creator_index])
        if not creator:
            continue
        value = lambda name: first_line(raw_row[indexes[name]]) if indexes[name] is not None and len(raw_row) > indexes[name] else ""
        current_diamonds = value("diamonds")
        source_tier = value("tier")
        previous_row = previous_by_creator.get(creator.casefold(), [])
        previous_diamonds = (
            previous_row[prior_diamond_index]
            if isinstance(previous_row, list) and prior_diamond_index is not None and len(previous_row) > prior_diamond_index
            else ""
        )
        comparison = compare_months(current_diamonds, previous_diamonds)
        tier_text = source_tier or comparison.current_tier or "Below 100K"
        source_tier_lower = source_tier.casefold()
        if "rank" in source_tier_lower or comparison.outcome == "Moved up":
            movement = "Ranking up"
        elif "maintain" in source_tier_lower or comparison.outcome == "Maintained":
            movement = "Maintaining tier"
        else:
            movement = "—"
        rows.append({
            "creator": creator,
            "manager": value("manager") or "Unassigned",
            "diamonds": current_diamonds or "—",
            "diamond_amount": number_in_cell(current_diamonds) or 0,
            "live_days": value("live_days") or "—",
            "live_duration": value("live_duration") or "—",
            "bonus": value("bonus") or "—",
            "tier": tier_text,
            "activeness": value("activeness") or "—",
            "movement": movement,
        })
    managers = sorted({str(row["manager"]) for row in rows if str(row["manager"]) != "Unassigned"}, key=str.casefold)
    return {"rows": rows, "managers": managers}


@app.get("/goals/{view_name}", response_class=HTMLResponse)
async def goals(view_name: str, request: Request) -> str:
    require_dashboard_user(request, database())
    if view_name not in {"managers", "creators"}:
        raise HTTPException(404, "Goal view not found")
    if view_name == "creators":
        return RedirectResponse("/goals/managers", status_code=303)
    snapshot = database().latest_snapshot("goals", "creators")
    if snapshot is None:
        return page("Monthly Goals", "<p>No monthly goal snapshot has been synced yet.</p>")
    if view_name == "managers":
        previous = database().previous_snapshot("goals", "creators", str(snapshot.get("month") or ""))
        data = creator_goal_data(snapshot, previous)
        if not data:
            return page(
                "Managers",
                "<section class='panel'><h2>Creator view is waiting for verification</h2>"
                "<p>The latest import did not contain Creator rows, so no Manager information is shown here by mistake.</p></section>",
            )
        selected_manager = request.query_params.get("manager", "").strip()
        manager_names = data["managers"]
        if selected_manager not in manager_names:
            selected_manager = ""
        creator_rows = [
            row for row in data["rows"]
            if not selected_manager or row["manager"] == selected_manager
        ]
        maintaining = sum(row["movement"] == "Maintaining tier" for row in creator_rows)
        ranking_up = sum(row["movement"] == "Ranking up" for row in creator_rows)
        above_200k = sum(int(row["diamond_amount"]) >= 200_000 for row in creator_rows)
        tabs = "<a class='{}' href='/goals/managers'>All managers</a>".format("current" if not selected_manager else "")
        tabs += "".join(
            f"<a class='{'current' if name == selected_manager else ''}' href='/goals/managers?manager={quote(name)}'>{html.escape(name)}</a>"
            for name in manager_names
        )
        row_html = "".join(
            "<tr>"
            f"<td class='creator-name'>{html.escape(str(row['creator']))}</td>"
            f"<td>{html.escape(str(row['diamonds']))}</td>"
            f"<td>{html.escape(str(row['live_days']))}</td>"
            f"<td>{html.escape(str(row['live_duration']))}</td>"
            f"<td>{html.escape(str(row['bonus']))}</td>"
            f"<td>{html.escape(str(row['tier']))}</td>"
            f"<td>{html.escape(str(row['activeness']))}</td>"
            f"<td>{html.escape(str(row['movement']))}</td>"
            "</tr>"
            for row in creator_rows
        ) or "<tr><td colspan='8'>No creators are assigned to this manager in the latest capture.</td></tr>"
        title = f"Managers — {snapshot.get('month', '')}"
        caption = selected_manager or "All managers"
        return page(title, f"""
        <p>Latest verified Creator capture: {html.escape(str(snapshot.get('captured_at', '')))} · {html.escape(caption)}</p>
        <div class='tabs'>{tabs}</div>
        <div class='metrics'><div class='metric'><strong>{len(creator_rows)}</strong><span>Creators</span></div><div class='metric'><strong>{maintaining}</strong><span>Maintaining tier</span></div><div class='metric'><strong>{ranking_up}</strong><span>Ranking up</span></div><div class='metric'><strong>{above_200k}</strong><span>At or above 200K</span></div></div>
        <table class='creator-table'><tr><th>Creator</th><th>Diamonds</th><th>Valid go LIVE days</th><th>Valid LIVE duration</th><th>Bonus contribution</th><th>Tier</th><th>Activeness</th><th>Tier movement</th></tr>{row_html}</table>""")

    headers = snapshot.get("headers", [])
    rows = snapshot.get("rows", [])
    if not isinstance(headers, list):
        headers = []
    if not isinstance(rows, list):
        rows = []
    manager_index = header_index(headers, "Manager")
    header_html = "".join(f"<th>{html.escape(str(header))}</th>" for header in headers)
    row_html = "".join(
        "<tr>" + "".join(
            f"<td>{html.escape(first_line(cell) if index == manager_index else str(cell))}</td>"
            for index, cell in enumerate(row)
        ) + "</tr>"
        for row in rows if isinstance(row, list)
    ) or "<tr><td>No manager goal rows have been received yet.</td></tr>"
    return page(
        f"Manager Goals — {snapshot.get('month', '')}",
        f"<p>Latest capture: {html.escape(str(snapshot.get('captured_at', '')))}</p><table><tr>{header_html}</tr>{row_html}</table>",
    )


@app.get("/creator/{creator_id}", response_class=HTMLResponse)
async def creator(creator_id: str, request: Request) -> str:
    db = database()
    require_dashboard_user(request, db)
    profile = db.get_creator(creator_id=creator_id, handle=None)
    if profile is None:
        raise HTTPException(404, "Creator not found")
    creator_data = profile["creator"]
    manager = profile["manager"]
    sections = "".join(f"<h2>{html.escape(key.replace('_', ' ').title())}</h2><pre>{html.escape(str([raw_record(row) for row in profile[key]]))}</pre>" for key in ("goals", "applications", "business_essentials"))
    return page(creator_data["handle"] or creator_data["creator_name"] or "Creator", f"<h2>Creator record</h2><pre>{html.escape(str(raw_record(creator_data)))}</pre><h2>Manager</h2><pre>{html.escape(str(raw_record(manager))) if manager else 'No manager linked'}</pre>{sections}")


@app.get("/manager/{manager_id}", response_class=HTMLResponse)
async def manager(manager_id: str, request: Request) -> str:
    db = database()
    require_dashboard_user(request, db)
    profile = db.get_manager(manager_id=manager_id, name=None)
    if profile is None:
        raise HTTPException(404, "Manager not found")
    roster = "".join(f"<li><a href='/creator/{html.escape(row['creator_id'], quote=True)}'>{html.escape(row['handle'] or row['creator_name'] or row['creator_id'])}</a></li>" for row in profile["creators"]) or "<li>No assigned creators.</li>"
    applications = "".join(f"<li>{html.escape(str(raw_record(row)))}</li>" for row in profile["applications"]) or "<li>No applications.</li>"
    return page(profile["manager"]["manager_name"], f"<h2>Assigned creators</h2><ul>{roster}</ul><h2>Scout Creator applications</h2><ul>{applications}</ul>")


@app.get("/access", response_class=HTMLResponse)
async def access_management(request: Request) -> str:
    db = database()
    actor = require_access_admin(request, db)
    rows = ""
    for user in db.access_users():
        active = bool(user["active"])
        status = "Active" if active else "Removed"
        action = ""
        if active and user["email"] != actor["email"]:
            action = (
                f"<form method='post' action='/access/users/{quote(str(user['email']), safe='')}/remove'>"
                "<button type='submit'>Remove access</button></form>"
            )
        state_class = "active" if active else "inactive"
        rows += (
            f"<tr><td>{html.escape(str(user['email']))}</td><td>{html.escape(str(user['role']).title())}</td>"
            f"<td class='{state_class}'>{status}</td><td>{action}</td></tr>"
        )
    owner_option = "<option value='owner'>Owner — full control</option>" if actor["role"] == "owner" else ""
    return page("Access Management", f"""<p>Signed in as {html.escape(str(actor['email']))}. Removed members remain in this audit list but cannot open the dashboard.</p>
    <h2>Add or restore a member</h2><form method='post' action='/access/users'><input type='email' name='email' placeholder='name@example.com' required><select name='role'><option value='member'>Member — view dashboard</option><option value='admin'>Administrator — manage access</option>{owner_option}</select><button type='submit'>Save access</button></form>
    <h2>Team access</h2><table><tr><th>Email</th><th>Role</th><th>Status</th><th></th></tr>{rows}</table>""")


@app.post("/access/users")
async def add_access_user(request: Request, email: str = Form(), role: str = Form("member")) -> RedirectResponse:
    db = database()
    actor = require_access_admin(request, db)
    if role == "owner" and actor["role"] != "owner":
        raise HTTPException(403, "Only an owner can add another owner.")
    try:
        db.add_access_user(email, role)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return RedirectResponse("/access", status_code=303)


@app.post("/access/users/{email}/remove")
async def remove_access_user(email: str, request: Request) -> RedirectResponse:
    db = database()
    require_access_admin(request, db)
    try:
        db.deactivate_access_user(email)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return RedirectResponse("/access", status_code=303)


def raw_record(row: dict[str, str] | None) -> dict[str, object]:
    if row is None:
        return {}
    return json.loads(row["raw_json"]) if row.get("raw_json") else row


def avatar(creator: dict[str, object]) -> str:
    """Render a remote Backstage avatar only when the authorized capture supplied one."""
    url = str(creator.get("profile_image_url") or "").strip()
    if not url.startswith("https://"):
        return ""
    return f"<img src='{html.escape(url, quote=True)}' alt='' referrerpolicy='no-referrer' style='width:38px;height:38px;border-radius:50%;object-fit:cover'>"
