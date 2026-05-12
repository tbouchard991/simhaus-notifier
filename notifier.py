"""
Simhaus Time Attack — Discord Notifier + Data Proxy
Runs via GitHub Actions every 5 minutes.
- Saves results.json for the leaderboard to fetch
- Saves known_bests.json for PB tracking
- Posts to Discord on new PBs, P1 changes, class leader changes, rival alerts
"""

import json
import os
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── Config ────────────────────────────────────
STRACKER_BASE   = 'https://usa2.assettohosting.com:50640/stracker/lapstat'
DISCORD_WEBHOOK = os.environ.get('DISCORD_WEBHOOK', '')
KNOWN_BESTS_FILE = 'known_bests.json'
RESULTS_FILE     = 'results.json'
RIVAL_GAP_MS     = 200  # 0.2 seconds

# All car IDs — used for filter POST
ALL_CARS = [
    'pib_e36_TA',
    'mrkryp_hgk_toyota_supra_tuerk_timeattack',
    's2000_2003_time_attack',
    's7r_s2000_r1',
    'mh_bmw_m3_e46_s2',
    'tw_bmw_m4_lenz',
    'toy_supra98_track',
    'bkr_toyota_gr86_timeattack',
    's281_2000_track',
    'project9_nissan_380rs_sundome',
    'project9_nissan_gtr_mcr_r34',
    'rbms_honda_nsx_advance_na2',
    'rbms_rx7_20b',
    'corvette_z06_track',
    '996_2001_track',
    'honda_spoon_fit_gd3',
    'honda_spoon_fit_gd3_nofsb_sundaecup',
    'bmw_m3_e92_team_schirmer_by_freeman',
]

CAR_LABELS = {
    'pib_e36_TA':                               'PIB BMW E36 TA',
    'mrkryp_hgk_toyota_supra_tuerk_timeattack': 'HGK Toyota Supra',
    's2000_2003_time_attack':                   'Honda S2000 TA',
    's7r_s2000_r1':                             'S7R S2000 R1',
    'mh_bmw_m3_e46_s2':                         'BMW M3 E46 S2',
    'tw_bmw_m4_lenz':                           'BMW M4 Lenz',
    'toy_supra98_track':                        'Toyota Supra 98',
    'bkr_toyota_gr86_timeattack':               'Toyota GR86 TA',
    's281_2000_track':                          'Saleen S281',
    'project9_nissan_380rs_sundome':            'Nissan 380RS',
    'project9_nissan_gtr_mcr_r34':              'Nissan GT-R R34',
    'rbms_honda_nsx_advance_na2':               'Honda NSX NA2',
    'rbms_rx7_20b':                             'Mazda RX-7 20B',
    'corvette_z06_track':                       'Corvette Z06',
    '996_2001_track':                           'Porsche 996 GT3',
    'honda_spoon_fit_gd3':                      'Honda Fit Spoon',
    'honda_spoon_fit_gd3_nofsb_sundaecup':      'Honda Fit Sunday Cup',
    'bmw_m3_e92_team_schirmer_by_freeman':      'BMW M3 E92',
}

def car_label(car_id):
    return CAR_LABELS.get(car_id, car_id)

# ── Lap time helpers ──────────────────────────
def norm_lap(t):
    if not t: return t
    return re.sub(r'^0(\d:)', r'\1', t)

def lap_to_ms(t):
    if not t or t == '—': return float('inf')
    t = norm_lap(t)
    parts = t.split(':')
    if len(parts) == 2:
        return float(parts[0]) * 60000 + float(parts[1]) * 1000
    return float(parts[0]) * 1000

# ── Fetch sTracker ────────────────────────────
import http.cookiejar

def get_session_with_all_cars():
    """Get a session cookie with all cars selected by using the admin interface."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    
    # First fetch the page to get initial session
    req = urllib.request.Request(
        STRACKER_BASE,
        headers={'User-Agent': 'Mozilla/5.0 SimhausNotifier/1.0'},
    )
    opener.open(req, timeout=15)
    return opener

def fetch_page(page, opener=None):
    if opener is None:
        opener = urllib.request.build_opener()
    url = f"{STRACKER_BASE}?page={page}"
    req = urllib.request.Request(
        url,
        headers={'User-Agent': 'Mozilla/5.0 SimhausNotifier/1.0'},
    )
    with opener.open(req, timeout=15) as r:
        return r.read().decode('utf-8', errors='replace')

import urllib.parse

def parse_laps(html):
    laps = []
    row_re  = re.compile(r'href="lapdetails\?lapid=(\d+)#">([\s\S]*?)</tr>')
    td_re   = re.compile(r'<td[^>]*>([\s\S]*?)</td>')
    tag_re  = re.compile(r'<[^>]+>')
    vmax_re = re.compile(r'vmax[^>]*>([\d.]+)')

    for m in row_re.finditer(html):
        lapid    = m.group(1)
        row_html = m.group(2)
        cells    = [tag_re.sub('', td.group(1)).strip() for td in td_re.finditer(row_html)]
        if len(cells) < 8: continue
        vm = vmax_re.search(row_html)
        laps.append({
            'lapid':  lapid,
            'pos':    cells[0].replace('.', '').strip(),
            'driver': cells[1].strip(),
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
    """Fetch each car individually to bypass the session filter."""
    seen, unique = set(), []
    
    for car in ALL_CARS:
        try:
            url = f"{STRACKER_BASE}?page=0&cars={urllib.parse.quote(car)}&trackname=ks_laguna_seca&ranking=mulcarmuldrv"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 SimhausNotifier/1.0'})
            with urllib.request.urlopen(req, timeout=15) as r:
                html = r.read().decode('utf-8', errors='replace')
            for l in parse_laps(html):
                if l['lapid'] not in seen:
                    seen.add(l['lapid'])
                    unique.append(l)
        except Exception as e:
            print(f"Failed to fetch {car}: {e}")
            continue
    
    # Sort by lap time and recalculate positions
    unique.sort(key=lambda l: lap_to_ms(l['lap']))
    if unique:
        p1_ms = lap_to_ms(unique[0]['lap'])
        for i, l in enumerate(unique):
            l['pos'] = str(i + 1)
            l['gap'] = '+00.000' if i == 0 else f"+{(lap_to_ms(l['lap'])-p1_ms)/1000:06.3f}"
    
    return unique

# ── Persistence ───────────────────────────────
def load_bests():
    if os.path.exists(KNOWN_BESTS_FILE):
        with open(KNOWN_BESTS_FILE) as f:
            return json.load(f)
    return {'pb': {}, 'overall_p1': {}, 'class_leaders': {}, 'rivals_posted': {}}

def save_bests(data):
    with open(KNOWN_BESTS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def save_results(laps):
    data = {
        'ok': True,
        'updated': datetime.now(timezone.utc).isoformat(),
        'count': len(laps),
        'laps': laps,
    }
    with open(RESULTS_FILE, 'w') as f:
        json.dump(data, f)
    print(f"Saved {len(laps)} laps to {RESULTS_FILE}")

# ── Discord ───────────────────────────────────
def post_discord(embed):
    if not DISCORD_WEBHOOK:
        print('No webhook configured')
        return
    payload = json.dumps({'embeds': [embed]}).encode('utf-8')
    req = urllib.request.Request(
        DISCORD_WEBHOOK,
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f'Discord: {r.status}')
    except urllib.error.HTTPError as e:
        print(f'Discord error: {e.code} {e.read()}')

def ts():
    return datetime.now(timezone.utc).isoformat()

# ── Check PBs ─────────────────────────────────
def check_pbs(laps, bests):
    pb = bests.setdefault('pb', {})
    new_pbs = []
    for l in laps:
        key  = f"{l['driver']}:{l['car']}"
        prev = pb.get(key)
        cur  = l['lap']
        if not prev:
            pb[key] = cur
            continue
        if lap_to_ms(cur) < lap_to_ms(prev):
            pb[key] = cur
            new_pbs.append(l)

    for l in new_pbs:
        if l['pos'] == '1': continue
        gap = l['gap'] if l['gap'] and l['gap'] != '+' else '—'
        post_discord({
            'title': '🏎️ New Personal Best',
            'description': f"**{l['driver']}** just set a new best lap",
            'color': 0x00d352,
            'fields': [
                {'name': '⏱️ Lap Time',   'value': f"`{l['lap']}`",   'inline': True},
                {'name': '🚗 Car',        'value': car_label(l['car']),'inline': True},
                {'name': '📍 Position',   'value': f"P{l['pos']}",    'inline': True},
                {'name': '📊 Gap to P1',  'value': gap,               'inline': True},
                {'name': '🔄 Total Laps', 'value': str(l['laps']),    'inline': True},
            ],
            'footer': {'text': 'Simhaus Time Attack · Laguna Seca'},
            'timestamp': ts(),
        })
        print(f"PB: {l['driver']} {l['lap']} in {car_label(l['car'])}")

# ── Check Overall P1 ──────────────────────────
def check_overall_p1(laps, bests):
    if not laps: return
    p1   = laps[0]
    prev = bests.get('overall_p1', {})
    if not prev:
        bests['overall_p1'] = {'driver': p1['driver'], 'lap': p1['lap']}
        return
    is_new = prev['driver'] != p1['driver']
    faster = lap_to_ms(p1['lap']) < lap_to_ms(prev['lap'])
    if not is_new and not faster: return

    post_discord({
        'title': '🏆 New Overall P1!',
        'description': f"**{p1['driver']}** has taken the overall lead from **{prev['driver']}**!" if is_new else f"**{p1['driver']}** improved their overall P1!",
        'color': 0xFFD700,
        'fields': [
            {'name': '⏱️ New Lap',   'value': f"`{p1['lap']}`",    'inline': True},
            {'name': '🚗 Car',       'value': car_label(p1['car']),'inline': True},
            {'name': '📉 Prev Best', 'value': f"`{prev['lap']}`",  'inline': True},
        ],
        'footer': {'text': 'Simhaus Time Attack · Laguna Seca'},
        'timestamp': ts(),
    })
    print(f"P1: {p1['driver']} {p1['lap']}")
    bests['overall_p1'] = {'driver': p1['driver'], 'lap': p1['lap']}

# ── Check Class Leaders ───────────────────────
def check_class_leaders(laps, bests):
    leaders = bests.setdefault('class_leaders', {})
    by_car  = {}
    for l in laps:
        if l['car'] not in by_car:
            by_car[l['car']] = l

    for car_id, fastest in by_car.items():
        prev = leaders.get(car_id)
        if not prev:
            leaders[car_id] = {'driver': fastest['driver'], 'lap': fastest['lap']}
            continue
        is_new = prev['driver'] != fastest['driver']
        faster = lap_to_ms(fastest['lap']) < lap_to_ms(prev['lap'])
        if not is_new and not faster: continue

        post_discord({
            'title': f"🏅 Class Leader Change — {car_label(car_id)}",
            'description': f"**{fastest['driver']}** has taken P1 in class from **{prev['driver']}**!" if is_new else f"**{fastest['driver']}** improved their class best!",
            'color': 0x5ab4e8,
            'fields': [
                {'name': '⏱️ New Lap',   'value': f"`{fastest['lap']}`", 'inline': True},
                {'name': '📉 Prev Best', 'value': f"`{prev['lap']}`",    'inline': True},
            ],
            'footer': {'text': 'Simhaus Time Attack · Laguna Seca'},
            'timestamp': ts(),
        })
        print(f"Class leader: {fastest['driver']} in {car_label(car_id)}")
        leaders[car_id] = {'driver': fastest['driver'], 'lap': fastest['lap']}

# ── Check Rivals ──────────────────────────────
def check_rivals(laps, bests):
    posted = bests.setdefault('rivals_posted', {})
    today  = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    for i in range(len(laps) - 1):
        a, b = laps[i], laps[i+1]
        if a['car'] != b['car']: continue
        gap_ms = lap_to_ms(b['lap']) - lap_to_ms(a['lap'])
        if gap_ms <= 0 or gap_ms > RIVAL_GAP_MS: continue
        key = f"{a['driver']}:{b['driver']}:{a['car']}"
        if posted.get(key) == today: continue
        posted[key] = today
        post_discord({
            'title': f"⚔️ Close Battle — {car_label(a['car'])}",
            'description': f"**{b['driver']}** is only **{gap_ms/1000:.3f}s** behind **{a['driver']}**!",
            'color': 0xff6b00,
            'fields': [
                {'name': f"🥇 {a['driver']}", 'value': f"`{a['lap']}`",       'inline': True},
                {'name': f"🥈 {b['driver']}", 'value': f"`{b['lap']}`",       'inline': True},
                {'name': '📏 Gap',            'value': f"{gap_ms/1000:.3f}s", 'inline': True},
            ],
            'footer': {'text': 'Simhaus Time Attack · Laguna Seca'},
            'timestamp': ts(),
        })
        print(f"Rival: {b['driver']} vs {a['driver']}")

# ── Main ──────────────────────────────────────
def main():
    print(f"Running at {datetime.now(timezone.utc).strftime('%H:%M UTC')}")

    try:
        laps = fetch_all_laps()
        print(f"Fetched {len(laps)} laps")
    except Exception as e:
        print(f"Failed to fetch: {e}")
        return

    if not laps:
        print("No laps — check sTracker connection")
        return

    # Save results for leaderboard
    save_results(laps)

    # Discord notifications
    bests = load_bests()
    check_pbs(laps, bests)
    check_overall_p1(laps, bests)
    check_class_leaders(laps, bests)
    check_rivals(laps, bests)
    save_bests(bests)
    print("Done")

if __name__ == '__main__':
    main()
