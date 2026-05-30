"""
Simhaus Time Attack — Data Sync
Fetches sTracker and saves results.json for the leaderboard.
Discord notifications handled by Cloudflare Worker cron trigger.
"""

import json, os, re, urllib.request, urllib.error
from datetime import datetime, timezone

STRACKER_BASE    = 'https://usa2.assettohosting.com:50640/stracker/lapstat'
STRACKER_SESSION = os.environ.get('STRACKER_SESSION', 'fb65464a4e5f7132bb703ac5ccffd1c405796f38')
RESULTS_FILE     = 'results.json'

TRACK      = 'njmp_lightning'
EVENT_CARS = 'blckbox_f1600_mygale,bmw_e36_compact,legends_ford_34_coupe'

DRIVER_NAMES = {
    'Bautista Bordes': 'zimmer\u2074\u2074',
    'Sauce':           'Hugie',
}

def driver_name(n): return DRIVER_NAMES.get(n, n)
def norm_lap(t):
    if not t: return t
    return re.sub(r'^0(\d:)', r'\1', t)
def lap_to_ms(t):
    if not t: return float('inf')
    t = norm_lap(t)
    parts = t.split(':')
    return float(parts[0])*60000 + float(parts[1])*1000 if len(parts)==2 else float(parts[0])*1000

def fetch_page(page):
    url = f"{STRACKER_BASE}?track={TRACK}&cars={EVENT_CARS}&valid=1,2,0&date_from=&date_to=&page={page}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    if STRACKER_SESSION:
        headers['Cookie'] = f'session_id={STRACKER_SESSION}'
        print(f"Using session: {STRACKER_SESSION[:8]}...")
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        html = r.read().decode('utf-8', errors='replace')
    print(f"Page {page}: {len(html)} chars")
    return html

def parse_laps(html):
    laps = []
    row_re  = re.compile(r'href="lapdetails\?lapid=(\d+)#">([\s\S]*?)</tr>')
    td_re   = re.compile(r'<td[^>]*>([\s\S]*?)</td>')
    tag_re  = re.compile(r'<[^>]+>')
    vmax_re = re.compile(r'vmax[^>]*>([\d.]+)')
    for m in row_re.finditer(html):
        cells = [tag_re.sub('', td.group(1)).strip() for td in td_re.finditer(m.group(2))]
        if len(cells) < 8: continue
        vm = vmax_re.search(m.group(2))
        laps.append({
            'lapid':  m.group(1),
            'pos':    cells[0].replace('.','').strip(),
            'driver': driver_name(cells[1].strip()),
            'car':    cells[2].strip(),
            'lap':    norm_lap(cells[3].strip()),
            'gap':    cells[4].strip(),
            's1':     cells[5].strip(),
            's2':     cells[6].strip(),
            's3':     cells[7].strip(),
            'valid':  cells[8].strip() if len(cells) > 8 else 'yes',
            'laps':   cells[10].strip() if len(cells) > 10 else '?',
            'date':   cells[11].strip() if len(cells) > 11 else '',
            'speed':  vm.group(1) if vm else '',
        })
    return laps

def fetch_all_laps():
    allowed = set(EVENT_CARS.split(','))
    seen, unique = set(), []
    for page in [0, 1]:
        try:
            html = fetch_page(page)
            for l in parse_laps(html):
                if l['lapid'] not in seen and l['car'] in allowed:
                    seen.add(l['lapid'])
                    unique.append(l)
        except Exception as e:
            print(f"Failed page {page}: {e}")

    unique.sort(key=lambda l: lap_to_ms(l['lap']))
    if unique:
        p1_ms = lap_to_ms(unique[0]['lap'])
        for i, l in enumerate(unique):
            l['pos'] = str(i + 1)
            l['gap'] = '+00.000' if i == 0 else f"+{(lap_to_ms(l['lap'])-p1_ms)/1000:06.3f}"
    print(f"Total laps: {len(unique)}")
    return unique

def main():
    print(f"Running at {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    try:
        laps = fetch_all_laps()
    except Exception as e:
        print(f"Failed: {e}"); return
    if not laps:
        print("No laps"); return
    with open(RESULTS_FILE, 'w') as f:
        json.dump({'ok': True, 'updated': datetime.now(timezone.utc).isoformat(), 'count': len(laps), 'laps': laps}, f)
    print(f"Saved {len(laps)} laps. Done")

if __name__ == '__main__':
    main()
