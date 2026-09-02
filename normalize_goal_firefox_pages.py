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
        avatar_url = ''
        creator_nodes = [
            node for node in page.get('layout', [])
            if node.get('text') == item[0] and 300 <= float(node.get('x', 0)) <= 520
        ]
        if creator_nodes:
            creator_y = float(creator_nodes[0].get('y', 0))
            avatar_candidates = [
                image for image in page.get('images', [])
                if 250 <= float(image.get('x', 0)) <= 520
                and abs(float(image.get('y', 0)) - creator_y) <= 45
                and image.get('src')
            ]
            if avatar_candidates:
                avatar_url = min(
                    avatar_candidates,
                    key=lambda image: abs(float(image.get('y', 0)) - creator_y),
                )['src']
        if email_at < 2 or group_at != email_at + 2 or len(item) < group_at + 9:
            raise RuntimeError(f'Unexpected Creator fields: {item!r}')
        tail = item[group_at + 2:]
        # The current Creator view emits the value and target on separate
        # lines for the first four metrics.  Keep each pair together so a
        # target fragment can never shift into the next database column.
        if len(tail) < 8:
            raise RuntimeError(f'Unexpected Creator metric fields: {item!r}')
        metrics = tail[:-4]
        parsed_metrics = []
        metric_at = 0
        for _ in range(4):
            if metric_at >= len(metrics):
                raise RuntimeError(f'Unexpected Creator metric pairs: {item!r}')
            current_value = metrics[metric_at]
            metric_at += 1
            target_value = ''
            if metric_at < len(metrics) and metrics[metric_at].startswith('/'):
                target_value = metrics[metric_at]
                metric_at += 1
            parsed_metrics.append((current_value, target_value))
        diamonds, live_days, live_duration, bonus = parsed_metrics
        status_fields = tail[metric_at:]
        # When the activeness target is present (for example /Level 5), it is
        # an optional fifth status line and must not shift Tier or Rank-up.
        if len(status_fields) < 4:
            raise RuntimeError(f'Unexpected Creator status fields: {item!r}')
        rows.append({
            'creator': item[0],
            'creator_id': item[1] if item[1].isdigit() else item[0].casefold(),
            'avatar_url': avatar_url,
            'manager': item[2] if email_at > 2 else 'Unassigned',
            'manager_role': ' '.join(item[3:email_at]) if email_at > 3 else '',
            'manager_email': item[email_at + 1],
            'group': item[group_at + 1],
            'diamonds': diamonds[0],
            'diamonds_display': ' '.join(x for x in diamonds if x),
            'valid_live_days': live_days[0],
            'valid_live_days_display': ' '.join(x for x in live_days if x),
            'valid_live_duration': live_duration[0],
            'valid_live_duration_display': ' '.join(x for x in live_duration if x),
            'bonus': bonus[0],
            'bonus_display': ' '.join(x for x in bonus if x),
            'tier': status_fields[0],
            'tier_status': status_fields[0],
            'rank_up_status': status_fields[1],
            'rank_up_detail': status_fields[2],
            'activeness': ' '.join(status_fields[3:]),
            'is_live': False,
        })
expected = pages[0]['showing'][2]
if len(pages) < 2 or len(rows) != expected or len({x['creator_id'] for x in rows}) != expected or pages[-1]['showing'][1] != expected:
    raise RuntimeError(f'Creator validation failed: pages={len(pages)} rows={len(rows)} expected={expected} last={pages[-1]["showing"]}')
summary_lines = [x.strip() for x in pages[0]['text'].splitlines() if x.strip()]
new_creator_at = summary_lines.index('New creators')
new_creators = int(re.sub(r'[^0-9]', '', summary_lines[new_creator_at + 1]))
managers = sorted({x['manager'] for x in rows})
payload = {'captured_at': datetime.now(timezone.utc).isoformat(), 'source': 'goal-management-creator-firefox', 'creator_pages': len(pages), 'creator_count': len(rows), 'expected_total': expected, 'manager_count': len(managers), 'meta': {'new_creators': new_creators}, 'publish_allowed': True, 'creators': rows}
target = Path('data/goal-creators-candidate.json')
temp = target.with_suffix('.tmp')
temp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
temp.replace(target)
print('VALIDATED_CREATOR_CANDIDATE', len(pages), len(rows), len(managers), expected)
