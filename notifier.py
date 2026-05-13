"""
Simhaus Time Attack — Discord Notifier + Data Proxy
Runs via GitHub Actions every 5 minutes.
"""

import json, os, re, urllib.request, urllib.error, urllib.parse, http.cookiejar
from datetime import datetime, timezone

# ── Config ────────────────────────────────────
STRACKER_BASE    = 'https://usa2.assettohosting.com:50640/stracker/lapstat'
STRACKER_SESSION = os.environ.get('STRACKER_SESSION', '')
DISCORD_WEBHOOK  = os.environ.get('DISCORD_WEBHOOK', '')
KNOWN_BESTS_FILE = 'known_bests.json'
RESULTS_FILE     = 'results.json'

RIVAL_GAP_MS       = 200    # 0.2s — rival alert threshold
MIN_IMPROVEMENT_MS = 100    # 0.1s — minimum improvement to post PB
PB_COOLDOWN_MINS   = 10     # minutes between PB posts per driver
SESSION_IDLE_MINS  = 120    # minutes of inactivity before session summary

ALL_CARS_PARAM = ','.join([
    '996_2001_track','bkr_toyota_gr86_timeattack','bmw_m3_e92_team_schirmer_by_freeman',
    'corvette_z06_track','mh_bmw_m3_e46_s2','mrkryp_hgk_toyota_supra_tuerk_timeattack',
    'pib_e36_TA','pib_e36_ta','project9_nissan_380rs_sundome','project9_nissan_gtr_mcr_r34',
    'rbms_honda_nsx_advance_na2','rbms_rx7_20b','s2000_2003_time_attack','s281_2000_track',
    's7r_s2000_r1','toy_supra98_track','tw_bmw_m4_lenz','honda_spoon_fit_gd3',
    'honda_spoon_fit_gd3_nofsb_sundaecup',
])

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

DRIVER_NAMES = {
    'Bautista Bordes': 'zimmer\u2074\u2074',
    'Sauce':           'Hugie',
}

def car_label(c):  return CAR_LABELS.get(c, c)
def driver_name(n): return DRIVER_NAMES.get(n, n)

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

def fmt_gap(ms):
    """Format milliseconds as a clean gap string e.g. +2.312s"""
    return f"+{ms/1000:.3f}s"

def fmt_improvement(prev_ms, cur_ms):
    """Format improvement e.g. ▲ 0.423s faster"""
    diff = prev_ms - cur_ms
    return f"▲ {diff/1000:.3f}s faster"

def fmt_gap_to_p1(gap_str):
    """Clean up raw gap string e.g. +02.312 → +2.312s"""
    if not gap_str or gap_str in ('+', '—', '+00.000'): return 'P1'
    try:
        ms = float(gap_str.replace('+','')) * 1000
        return fmt_gap(ms)
    except:
        return gap_str

# ── Fetch sTracker ────────────────────────────
def fetch_page(page):
    url = (f"{STRACKER_BASE}?track=ks_laguna_seca"
           f"&cars={ALL_CARS_PARAM}"
           f"&valid=1,2,0&date_from=&date_to=&page={page}")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml',
    }
    if STRACKER_SESSION:
        headers['Cookie'] = f'session_id={STRACKER_SESSION}'
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode('utf-8', errors='replace')

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
    seen, unique = set(), []
    for page in [0, 1]:
        try:
            html = fetch_page(page)
            laps = parse_laps(html)
            print(f"Page {page}: {len(laps)} laps")
            for l in laps:
                if l['lapid'] not in seen:
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
    print(f"Total unique laps: {len(unique)}")
    return unique

# ── Persistence ───────────────────────────────
def load_bests():
    if os.path.exists(KNOWN_BESTS_FILE):
        with open(KNOWN_BESTS_FILE) as f:
            return json.load(f)
    return {
        'pb': {},
        'pb_posted_at': {},     # driver:car -> ISO timestamp of last PB post
        'overall_p1': {},
        'class_leaders': {},
        'rivals_posted': {},
        'last_activity': None,  # ISO timestamp of most recent lap date seen
        'session_summary_posted': None,  # ISO timestamp of last summary post
    }

def save_bests(data):
    with open(KNOWN_BESTS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def save_results(laps):
    with open(RESULTS_FILE, 'w') as f:
        json.dump({
            'ok': True,
            'updated': datetime.now(timezone.utc).isoformat(),
            'count': len(laps),
            'laps': laps,
        }, f)
    print(f"Saved {len(laps)} laps to {RESULTS_FILE}")

# ── Discord ───────────────────────────────────
def post_discord(embed):
    if not DISCORD_WEBHOOK:
        print('No webhook configured')
        return
    payload = json.dumps({'embeds': [embed]}).encode('utf-8')
    req = urllib.request.Request(
        DISCORD_WEBHOOK, data=payload,
        headers={
            'Content-Type': 'application/json',
            'User-Agent': 'DiscordBot (https://simhaus.com, 1.0)',
        }, method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f'Discord: {r.status}')
    except urllib.error.HTTPError as e:
        print(f'Discord error: {e.code} {e.read()}')

def ts(): return datetime.now(timezone.utc).isoformat()

# ── 1. Personal Bests ─────────────────────────
def check_pbs(laps, bests, session_pbs=None):
    pb         = bests.setdefault('pb', {})
    posted_at  = bests.setdefault('pb_posted_at', {})
    now        = datetime.now(timezone.utc)
    new_pbs    = []

    for l in laps:
        key  = f"{l['driver']}:{l['car']}"
        prev = pb.get(key)
        cur  = l['lap']

        if not prev:
            pb[key] = cur
            continue

        improvement_ms = lap_to_ms(prev) - lap_to_ms(cur)
        if improvement_ms <= 0:
            continue

        # Must improve by at least MIN_IMPROVEMENT_MS
        if improvement_ms < MIN_IMPROVEMENT_MS:
            pb[key] = cur
            print(f"PB too small ({improvement_ms:.0f}ms): {l['driver']} {cur}")
            continue

        # Check cooldown — skip if posted within PB_COOLDOWN_MINS
        last_posted = posted_at.get(key)
        if last_posted:
            elapsed = (now - datetime.fromisoformat(last_posted)).total_seconds() / 60
            if elapsed < PB_COOLDOWN_MINS:
                pb[key] = cur
                print(f"PB cooldown ({elapsed:.1f}min): {l['driver']} {cur}")
                continue

        pb[key] = cur
        posted_at[key] = now.isoformat()
        new_pbs.append((l, improvement_ms))
        if session_pbs is not None:
            session_pbs.append((l['driver'], l['car'], improvement_ms))

    for l, improvement_ms in new_pbs:
        if l['pos'] == '1': continue
        gap_display = fmt_gap_to_p1(l['gap'])
        post_discord({
            'title': '🏎️ New Personal Best',
            'description': f"**{l['driver']}** just went faster in the **{car_label(l['car'])}**",
            'color': 0x00d352,
            'fields': [
                {'name': '⏱️ Lap Time',    'value': f"`{l['lap']}`",                   'inline': True},
                {'name': '📍 Position',    'value': f"P{l['pos']}",                    'inline': True},
                {'name': '📈 Improvement', 'value': fmt_improvement(lap_to_ms(l['lap']) + improvement_ms, lap_to_ms(l['lap'])), 'inline': True},
                {'name': '📊 Gap to P1',   'value': gap_display,                       'inline': True},
                {'name': '🔄 Total Laps',  'value': str(l['laps']),                    'inline': True},
            ],
            'footer': {'text': 'Simhaus Time Attack · Laguna Seca'},
            'timestamp': ts(),
        })
        print(f"PB: {l['driver']} {l['lap']} (▲{improvement_ms/1000:.3f}s) in {car_label(l['car'])}")

# ── 2. Overall P1 ─────────────────────────────
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

    improvement = lap_to_ms(prev['lap']) - lap_to_ms(p1['lap'])
    post_discord({
        'title': '🏆 New Overall P1!',
        'description': (f"**{p1['driver']}** has taken the overall lead from **{prev['driver']}**!"
                        if is_new else
                        f"**{p1['driver']}** improved their overall P1 by **{improvement/1000:.3f}s**!"),
        'color': 0xFFD700,
        'fields': [
            {'name': '⏱️ New Lap',    'value': f"`{p1['lap']}`",                  'inline': True},
            {'name': '🚗 Car',        'value': car_label(p1['car']),               'inline': True},
            {'name': '📉 Prev Best',  'value': f"`{prev['lap']}`",                 'inline': True},
        ],
        'footer': {'text': 'Simhaus Time Attack · Laguna Seca'},
        'timestamp': ts(),
    })
    print(f"P1: {p1['driver']} {p1['lap']}")
    bests['overall_p1'] = {'driver': p1['driver'], 'lap': p1['lap']}

# ── 3. Class Leaders ──────────────────────────
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

        improvement = lap_to_ms(prev['lap']) - lap_to_ms(fastest['lap'])
        post_discord({
            'title': f"🏅 Class Leader — {car_label(car_id)}",
            'description': (f"**{fastest['driver']}** has taken P1 in class from **{prev['driver']}**!"
                            if is_new else
                            f"**{fastest['driver']}** improved their class best by **{improvement/1000:.3f}s**!"),
            'color': 0x5ab4e8,
            'fields': [
                {'name': '⏱️ New Lap',   'value': f"`{fastest['lap']}`",           'inline': True},
                {'name': '📉 Prev Best', 'value': f"`{prev['lap']}`",              'inline': True},
                {'name': '📈 Delta',     'value': f"▲ {improvement/1000:.3f}s",    'inline': True},
            ],
            'footer': {'text': 'Simhaus Time Attack · Laguna Seca'},
            'timestamp': ts(),
        })
        print(f"Class leader: {fastest['driver']} in {car_label(car_id)}")
        leaders[car_id] = {'driver': fastest['driver'], 'lap': fastest['lap']}

# ── 4. Rivals ─────────────────────────────────
def check_rivals(laps, bests):
    posted = bests.setdefault('rivals_posted', {})
    today  = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    new_battles = []

    for i in range(len(laps) - 1):
        a, b = laps[i], laps[i+1]
        if a['car'] != b['car']: continue
        gap_ms = lap_to_ms(b['lap']) - lap_to_ms(a['lap'])
        if gap_ms <= 0 or gap_ms > RIVAL_GAP_MS: continue
        key = f"{a['driver']}:{b['driver']}:{a['car']}"
        if posted.get(key) == today: continue
        posted[key] = today
        new_battles.append((a, b, gap_ms))

    if not new_battles: return

    # Build single combined embed
    lines = '\n\n'.join(
        f"**{car_label(a['car'])}**\n{a['driver']} `{a['lap']}` vs {b['driver']} `{b['lap']}` · **{gap_ms/1000:.3f}s**"
        for a, b, gap_ms in new_battles
    )

    title = f"⚔️ Close Battle{'s' if len(new_battles)>1 else ''} — {len(new_battles)} pair{'s' if len(new_battles)>1 else ''} within {RIVAL_GAP_MS/1000:.1f}s"

    post_discord({
        'title': title,
        'description': lines,
        'color': 0xff6b00,
        'footer': {'text': 'Simhaus Time Attack · Laguna Seca'},
        'timestamp': ts(),
    })
    print(f"Rivals: {len(new_battles)} close battle(s) posted")

# ── 5. Session Summary ────────────────────────
def check_session_summary(laps, bests, session_pbs=None):
    """Post a session summary when the server has been idle for SESSION_IDLE_MINS."""
    if not laps: return

    dates = [l['date'] for l in laps if l.get('date')]
    if not dates: return

    try:
        latest_date_str = max(dates)
        latest_dt = datetime.strptime(latest_date_str, '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
    except Exception:
        return

    now       = datetime.now(timezone.utc)
    idle_mins = (now - latest_dt).total_seconds() / 60

    bests['last_activity'] = latest_date_str

    last_summary = bests.get('session_summary_posted')
    today        = now.strftime('%Y-%m-%d')

    if idle_mins < SESSION_IDLE_MINS:
        return
    if last_summary and last_summary[:10] == today:
        return

    # ── Build stats ───────────────────────────
    by_car = {}
    for l in laps:
        if l['car'] not in by_car:
            by_car[l['car']] = l

    overall    = laps[0]
    total_laps = sum(int(l['laps']) for l in laps if str(l['laps']).isdigit())
    drivers    = len(set(l['driver'] for l in laps))

    # Leaderboard snapshot — top 3 overall
    medals = ['🥇', '🥈', '🥉']
    podium = '\n'.join(
        f"{medals[i]} **{laps[i]['driver']}** `{laps[i]['lap']}` — {car_label(laps[i]['car'])}"
        for i in range(min(3, len(laps)))
    )

    # Class leaders
    class_lines = '\n'.join(
        f"**{car_label(car_id)}** — {e['driver']} `{e['lap']}`"
        for car_id, e in by_car.items()
    )

    # Most active driver
    driver_laps = {}
    for l in laps:
        n = int(l['laps']) if str(l['laps']).isdigit() else 0
        driver_laps[l['driver']] = driver_laps.get(l['driver'], 0) + n
    most_active = max(driver_laps.items(), key=lambda x: x[1], default=('—', 0))

    # New PBs set this session
    pbs_count = len(session_pbs) if session_pbs else 0

    # Biggest improvement this session
    biggest = None
    if session_pbs:
        biggest = max(session_pbs, key=lambda x: x[2])

    # Fun stat — total distance driven + average lap time
    TRACK_KM = 3.602
    total_km = total_laps * TRACK_KM

    # Average best lap time across all drivers
    valid_ms = [lap_to_ms(l['lap']) for l in laps if lap_to_ms(l['lap']) != float('inf')]
    if valid_ms:
        avg_ms   = sum(valid_ms) / len(valid_ms)
        avg_mins = int(avg_ms // 60000)
        avg_secs = (avg_ms % 60000) / 1000
        avg_str  = f"{avg_mins}:{avg_secs:06.3f}"
        fastest_ms = lap_to_ms(overall['lap'])
        delta_ms   = avg_ms - fastest_ms
        fun_stat = (
            f"{total_laps} laps = **{total_km:.0f} km** ({total_km*0.621:.0f} miles) driven today\n"
            f"Average best lap: **{avg_str}** — {delta_ms/1000:.3f}s off the overall fastest"
        )
    else:
        fun_stat = f"{total_laps} laps = **{total_km:.0f} km** ({total_km*0.621:.0f} miles) driven today"

    # Build fields
    fields = [
        {'name': '🏆 Top 3',          'value': podium or '—',       'inline': False},
        {'name': '🏅 Class Leaders',   'value': class_lines or '—',  'inline': False},
    ]

    # PBs set + biggest improvement on same row
    pb_val = f"{pbs_count} personal best{'s' if pbs_count!=1 else ''} broken"
    if biggest:
        pb_val += f"\nBiggest: **{biggest[0]}** ▲ {biggest[2]/1000:.3f}s in {car_label(biggest[1])}"
    fields.append({'name': '📈 Improvements', 'value': pb_val, 'inline': False})

    fields += [
        {'name': '🔄 Most Active', 'value': f"**{most_active[0]}** — {most_active[1]} laps", 'inline': True},
        {'name': '👥 Drivers',     'value': str(drivers),                                     'inline': True},
        {'name': '🔢 Total Laps',  'value': str(total_laps),                                  'inline': True},
        {'name': '🛣️ Fun Stat',    'value': fun_stat,                                         'inline': False},
    ]

    post_discord({
        'title': '📋 Session Summary',
        'description': f"The server went quiet after **{latest_date_str}**. Here's how the session wrapped up.",
        'color': 0x1e90ff,
        'fields': fields,
        'footer': {'text': 'Simhaus Time Attack · Laguna Seca · simhaus-leaderboard-2a7e2b.gitlab.io'},
        'timestamp': ts(),
    })
    bests['session_summary_posted'] = now.isoformat()
    print(f"Session summary posted (idle {int(idle_mins)} mins, {pbs_count} PBs)")

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

    save_results(laps)

    bests = load_bests()
    # Track session stats for summary
    session_pbs = []        # list of (driver, car, improvement_ms)
    check_pbs(laps, bests, session_pbs)
    check_overall_p1(laps, bests)
    check_class_leaders(laps, bests)
    check_rivals(laps, bests)
    check_session_summary(laps, bests, session_pbs)
    save_bests(bests)
    print("Done")

if __name__ == '__main__':
    main()
