import json
import re
from datetime import datetime, timezone
from pathlib import Path
pages = json.load(open('data/firefox-creator-pages.json', encoding='utf-8'))
rows = []
for page in pages:
    lines = [x.strip() for x in page['text'].splitlines() if x.strip() and x.strip() != '/Not set']
    start = lines.index('Action') + 1
    end = next(i for i, x in enumerate(lines[start:], start) if x.startswith('Showing '))
    chunk = lines[start:end]
    parts, current = [], []
    for value in chunk:
        if value == 'View details':
            parts.append(current); current = []
        else:
            current.append(value)
    page_size = page['showing'][1] - page['showing'][0] + 1
    for item in parts[:page_size]:
        if len(item) < 14 or 'Email' not in item or 'Group' not in item:
            raise RuntimeError(f'Unexpected Creator row: {item!r}')
        email_at, group_at = item.index('Email'), item.index('Group')
        if email_at < 2 or group_at != email_at + 2 or len(item) < group_at + 9:
            raise RuntimeError(f'Unexpected Creator fields: {item!r}')
        tail = item[group_at + 2:]
        rows.append({'creator': item[0], 'creator_id': item[1] if item[1].isdigit() else item[0].casefold(), 'manager': item[2] if email_at > 2 else 'Unassigned', 'manager_role': ' '.join(item[3:email_at]) if email_at > 3 else '', 'manager_email': item[email_at + 1], 'group': item[group_at + 1], 'diamonds': tail[0], 'valid_live_days': tail[1], 'valid_live_duration': tail[2], 'bonus': tail[3], 'tier': tail[6] if len(tail) > 8 else '', 'tier_status': tail[4], 'rank_up_status': tail[5], 'next_tier': tail[7] if len(tail) > 8 else '', 'activeness': (re.search(r'\d+', tail[-1]).group() if re.search(r'\d+', tail[-1]) else '0'), 'is_live': False})
expected = pages[0]['showing'][2]
if len(pages) < 2 or len(rows) != expected or len({x['creator_id'] for x in rows}) != expected or pages[-1]['showing'][1] != expected:
    raise RuntimeError(f'Creator validation failed: pages={len(pages)} rows={len(rows)} expected={expected} last={pages[-1]["showing"]}')
managers = sorted({x['manager'] for x in rows})
payload = {'captured_at': datetime.now(timezone.utc).isoformat(), 'source': 'goal-management-creator-firefox', 'creator_pages': len(pages), 'creator_count': len(rows), 'expected_total': expected, 'manager_count': len(managers), 'publish_allowed': True, 'creators': rows}
target = Path('data/goal-creators-candidate.json')
temp = target.with_suffix('.tmp')
temp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
temp.replace(target)
print('VALIDATED_CREATOR_CANDIDATE', len(pages), len(rows), len(managers), expected)
