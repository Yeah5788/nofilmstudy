#!/usr/bin/env python3
"""Hourly data refresh for nofilmstudy.com (runs in GitHub Actions, no Claude usage).

Reads data.json, pulls ESPN's public JSON feeds, merges into the same
{collection:{docId:doc}} shape the app reads, and writes data.json back.
Every source is wrapped so one failing feed never breaks the run, and
existing data is only replaced by fresh, non-empty data.
"""
import json, os, re, sys, time, unicodedata, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data.json")
APP = os.path.join(ROOT, "tools", "app.html")
NOW = datetime.now(timezone.utc)
ISO = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
NOWS = ISO(NOW)
LOG = []
STATS = {"calls": 0, "fail": 0}

def log(*a):
    s = " ".join(str(x) for x in a); LOG.append(s); print(s, flush=True)

def get(url, tries=2):
    for i in range(tries):
        STATS["calls"] += 1
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (nofilmstudy data bot)", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            err = e
            time.sleep(1 + i)
    STATS["fail"] += 1
    log("  ! fetch failed", url[:140], type(err).__name__, str(err)[:80])
    return None

SITE = "https://site.api.espn.com/apis/site/v2/sports/"
# app league -> list of (espn path, extra query, competition title, season pack)
LEAGUES = {
    "nfl":    [("football/nfl", "", None, "nfl")],
    "ncaaf":  [("football/college-football", "groups=80&limit=400", None, "ncaaf")],
    "nba":    [("basketball/nba", "", None, "nba")],
    "ncaab":  [("basketball/mens-college-basketball", "groups=50&limit=500", None, "ncaab-{mon}")],
    "nhl":    [("hockey/nhl", "", None, "nhl")],
    "mlb":    [("baseball/mlb", "", None, "mlb")],
    "soccer": [("soccer/eng.1", "", "Premier League", "soccer-epl"),
               ("soccer/esp.1", "", "La Liga", "soccer-laliga"),
               ("soccer/ita.1", "", "Serie A", "soccer-seriea"),
               ("soccer/ger.1", "", "Bundesliga", "soccer-bundesliga"),
               ("soccer/fra.1", "", "Ligue 1", "soccer-ligue1"),
               ("soccer/uefa.champions", "", "UEFA Champions League", "soccer-ucl"),
               ("soccer/usa.1", "", "MLS", None)],
    "ufc":    [("mma/ufc", "", None, None)],
}
INJ_LEAGUES = {"nfl": "football/nfl", "nba": "basketball/nba", "nhl": "hockey/nhl", "mlb": "baseball/mlb"}
MONTHS = ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"]

def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace("st.", "state").replace("univ.", "university")
    s = re.sub(r"\b(fc|cf|afc|sc|ac|the|university|of)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)

def slug(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")

def num(v):
    if v is None: return None
    if isinstance(v, (int, float)): return v
    s = str(v).strip().replace("−", "-")
    if s.upper() in ("EVEN", "EV", "PK", "PICK"): return 100 if s.upper().startswith("EV") else 0
    s = re.sub(r"^[ou]", "", s, flags=re.I)
    try:
        f = float(s); return int(f) if f.is_integer() and "." not in s else f
    except Exception:
        return None

def et_date(iso):
    d = datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc) - timedelta(hours=5)
    return d.strftime("%Y-%m-%d")

def parse_iso(s):
    s = (s or "").replace("Z", "")
    for f in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try: return datetime.strptime(s[:19 if f.endswith("S") else 16], f).replace(tzinfo=timezone.utc)
        except Exception: pass
    return None

# ---------------------------------------------------------------- load
D = json.load(open(DATA))
for c in ("games", "teams", "sched", "seasons", "meta", "leans", "standings"):
    D.setdefault(c, {})
G, T, M = D["games"], D["teams"], D["meta"]
EMAP = M.setdefault("espn", {"note": "ESPN team id -> app abbreviation map (auto)", "map": {}, "ids": {}, "cursor": {}})
EMAP.setdefault("map", {}); EMAP.setdefault("ids", {}); EMAP.setdefault("cursor", {})

# app's built-in team dictionaries (name/nick/colors) for pro leagues
APPT = {}
try:
    src = open(APP, encoding="utf-8").read()
    for lg in ("nfl", "nba", "nhl", "soccer", "mlb"):
        m = re.search(r"\b" + lg + r":\{([A-Z0-9]{2,5}:\[.*?\])\}", src)
        if not m: continue
        APPT[lg] = {ab: (n, k) for ab, n, k in re.findall(r'([A-Z0-9]{2,5}):\["([^"]*)","([^"]*)"', m.group(1))}
except Exception as e:
    log("app team dict parse failed", e)

def build_index(lg):
    idx = {}
    def add(key, ab):
        k = norm(key)
        if k and k not in idx: idx[k] = ab
    for ab, (n, k) in APPT.get(lg, {}).items():
        add(n, ab)
    for key, t in T.items():
        if not key.startswith(lg + "-"): continue
        ab = key[len(lg) + 1:]
        add(t.get("name"), ab)
    if lg == "ncaab":
        for n, ab in (M.get("seasons", {}).get("ncaab_names") or {}).items(): add(n, ab)
    for key, t in T.items():
        if key.startswith(lg + "-") and t.get("nick") and lg not in ("ncaab", "ncaaf"):
            add(t.get("nick"), key[len(lg) + 1:])
    return idx

IDX = {}
ALIAS = {"nba": {"GS": "GSW", "NO": "NOP", "SA": "SAS", "NY": "NYK", "UTAH": "UTA", "WSH": "WAS"},
         "nfl": {"WSH": "WAS"}, "nhl": {"UTAH": "UTA"}}

def team_ab(lg, tm, color_ok=True):
    """Map an ESPN team object to the app abbreviation; create the team doc if new."""
    if not tm: return None
    eid = str(tm.get("id") or "")
    mkey = lg + ":" + eid
    if eid and mkey in EMAP["map"]: return EMAP["map"][mkey]
    if lg not in IDX: IDX[lg] = build_index(lg)
    idx = IDX[lg]
    ab = None
    for k in (tm.get("displayName"), tm.get("name") and tm.get("location") and f'{tm.get("location")} {tm.get("name")}',
              tm.get("shortDisplayName"), tm.get("location"), tm.get("nickname")):
        if k and norm(k) in idx: ab = idx[norm(k)]; break
    eab = (tm.get("abbreviation") or "").upper()
    if not ab and eab:
        cand = ALIAS.get(lg, {}).get(eab, eab)
        if f"{lg}-{cand}" in T or cand in APPT.get(lg, {}): ab = cand
    if not ab:
        ab = re.sub(r"[^A-Z0-9]", "", eab) or re.sub(r"[^A-Z]", "", (tm.get("displayName") or "X").upper())[:6]
        if f"{lg}-{ab}" in T and norm(T[f"{lg}-{ab}"].get("name")) != norm(tm.get("displayName")):
            ab = ab + "E" + eid[-3:]
        doc = T.setdefault(f"{lg}-{ab}", {})
        doc.setdefault("name", tm.get("displayName") or tm.get("name"))
        doc.setdefault("nick", tm.get("name") if lg not in ("ncaab", "ncaaf") else (tm.get("location") or tm.get("displayName")))
        if tm.get("color"): doc.setdefault("color", "#" + tm["color"])
        if tm.get("alternateColor"): doc.setdefault("color2", "#" + tm["alternateColor"])
        doc.setdefault("src", "ESPN")
        idx[norm(tm.get("displayName"))] = ab
        log("  + new team", lg, ab, tm.get("displayName"))
    if eid:
        EMAP["map"][mkey] = ab
        EMAP["ids"].setdefault(lg, {})[ab] = eid
    tdoc = T.setdefault(f"{lg}-{ab}", {})
    if not tdoc.get("name") and lg in APPT and ab in APPT[lg]:
        pass  # name comes from the app dict
    return ab

def fighter_ab(lg, comp):
    ath = comp.get("athlete") or {}
    nm = ath.get("displayName") or ath.get("fullName") or ""
    last = re.sub(r"[^A-Z]", "", unicodedata.normalize("NFKD", (ath.get("lastName") or nm.split(" ")[-1])).encode("ascii", "ignore").decode().upper())
    ab = last or "TBD"
    k = f"{lg}-{ab}"
    if k in T and norm(T[k].get("name")) != norm(nm):
        first = re.sub(r"[^A-Z]", "", (ath.get("firstName") or nm).upper())[:1]
        ab = first + last; k = f"{lg}-{ab}"
    d = T.setdefault(k, {})
    d.setdefault("name", nm); d.setdefault("nick", nm.split(" ")[-1] if nm else ab)
    if comp.get("records"): d["rec"] = comp["records"][0].get("summary") or d.get("rec")
    return ab

# ---------------------------------------------------------------- game id index
def gkey(lg, h, a, iso):
    return (lg, h, a, et_date(iso)) if iso else None

GIDX = {}
def index_game(gid, g):
    if not g.get("start") or not g.get("home"): return
    for off in (-1, 0, 1):
        d = (parse_iso(g["start"]) or NOW) + timedelta(days=off)
        GIDX.setdefault((g.get("league"), g["home"], g["away"], (d - timedelta(hours=5)).strftime("%Y-%m-%d")), gid)
for gid, g in G.items(): index_game(gid, g)
for sd in D["sched"].values():
    for g in sd.get("games", []) or []:
        if g.get("id"): index_game(g["id"], dict(g, league=g.get("league") or sd.get("league")))
EGID = M.setdefault("espnGames", {"note": "ESPN event id -> game doc id", "map": {}})["map"]

# ---------------------------------------------------------------- odds parse
def parse_odds(comp, old):
    ol = comp.get("odds") or []
    if not ol: return None
    o = ol[0]
    out = {}
    hto, ato = o.get("homeTeamOdds") or {}, o.get("awayTeamOdds") or {}
    ml = o.get("moneyline") or {}
    ps = o.get("pointSpread") or {}
    tot = o.get("total") or {}
    def pick(*vals):
        for v in vals:
            n = num(v)
            if n is not None: return n
        return None
    sp = pick((ps.get("home") or {}).get("close", {}).get("line"), o.get("spread"))
    if sp is None and o.get("details"):
        m = re.match(r"([A-Z0-9]+)\s+([-+]?\d+(?:\.\d+)?)", o["details"])
        if m:
            fav = m.group(1); v = float(m.group(2))
            htab = ((comp.get("competitors") or [{}])[0].get("team") or {}).get("abbreviation")
            for c in comp.get("competitors", []):
                if c.get("homeAway") == "home": htab = c["team"].get("abbreviation")
            sp = v if fav == htab else -v
    if sp is not None: out["spread"] = sp
    t = pick((tot.get("over") or {}).get("close", {}).get("line"), o.get("overUnder"))
    if t is not None: out["total"] = t
    mh = pick(hto.get("moneyLine"), (ml.get("home") or {}).get("close", {}).get("odds"))
    ma = pick(ato.get("moneyLine"), (ml.get("away") or {}).get("close", {}).get("odds"))
    if mh is not None and ma is not None and abs(mh) >= 100 and abs(ma) >= 100:
        out["mlH"], out["mlA"] = mh, ma
    if not out: return None
    out["book"] = ((o.get("provider") or {}).get("name") or "ESPN") + " via ESPN"
    op = {}
    so = num((ps.get("home") or {}).get("open", {}).get("line"))
    to = num((tot.get("over") or {}).get("open", {}).get("line"))
    mo, mao = num((ml.get("home") or {}).get("open", {}).get("odds")), num((ml.get("away") or {}).get("open", {}).get("odds"))
    if so is not None: op["spread"] = so
    if to is not None: op["total"] = to
    if mo is not None and mao is not None: op["mlH"], op["mlA"] = mo, mao
    prev_open = (old or {}).get("open")
    if prev_open: out["open"] = prev_open
    elif op: out["open"] = dict(op, src="ESPN open line")
    elif old and any(k in old for k in ("spread", "total", "mlH")):
        out["open"] = {k: old[k] for k in ("spread", "total", "mlH", "mlA") if k in old}
        out["open"]["src"] = "first stored line"
    else:
        out["open"] = {k: out[k] for k in ("spread", "total", "mlH", "mlA") if k in out}
        out["open"]["src"] = "first stored line (" + NOWS + ")"
    return out

# ---------------------------------------------------------------- scoreboards
STATUS = {"pre": "scheduled", "in": "inprogress", "post": "closed"}
FETCH_BOX, FETCH_WP = [], []
touched = {lg: 0 for lg in LEAGUES}
pack_rows = {}

def ab_of(lg, c):
    return fighter_ab(lg, c) if lg == "ufc" else team_ab(lg, c.get("team"))

def do_event(lg, ev, title, pack):
    comps = ev.get("competitions") or []
    for ci, comp in enumerate(comps):
        cs = comp.get("competitors") or []
        if len(cs) != 2: continue
        home = next((c for c in cs if c.get("homeAway") == "home"), cs[0])
        away = next((c for c in cs if c is not home), cs[1])
        h, a = ab_of(lg, home), ab_of(lg, away)
        if not h or not a: continue
        start = comp.get("date") or ev.get("date")
        st = parse_iso(start)
        if not st: continue
        start = ISO(st)
        eid = str(comp.get("id") or ev.get("id"))
        gid = EGID.get(eid) or GIDX.get((lg, h, a, et_date(start)))
        if not gid:
            gid = f"{lg}-e{eid}"
        EGID[eid] = gid
        g = G.get(gid)
        if g is None:
            # schedule-pack game promoted to a real doc
            base = {}
            for sd in D["sched"].values():
                for x in sd.get("games", []) or []:
                    if x.get("id") == gid: base = {k: v for k, v in x.items() if k != "id"}
            g = G[gid] = base
        stype = (comp.get("status") or ev.get("status") or {}).get("type") or {}
        status = STATUS.get(stype.get("state"), g.get("status") or "scheduled")
        if stype.get("name") in ("STATUS_POSTPONED", "STATUS_CANCELED"): status = "postponed"
        g.update({"league": lg, "home": h, "away": a, "start": start, "status": status, "espnId": eid})
        g.setdefault("title", title)
        if lg == "ufc":
            g["title"] = ((comp.get("type") or {}).get("abbreviation") or (comp.get("note")) or g.get("title") or "UFC")
            g["homeName"] = (home.get("athlete") or {}).get("displayName"); g["awayName"] = (away.get("athlete") or {}).get("displayName")
        if comp.get("venue", {}).get("fullName") and not g.get("venue"): g["venue"] = comp["venue"]["fullName"]
        g["schedSource"] = "ESPN scoreboard"
        if stype.get("state") in ("in", "post") and lg != "ufc":
            try:
                g["score"] = {"h": int(float(home.get("score"))), "a": int(float(away.get("score")))}
            except Exception: pass
            g["resultSrc"] = "ESPN scoreboard"
            if stype.get("state") == "in": g["clock"] = stype.get("shortDetail") or stype.get("detail")
            else: g.pop("clock", None)
            per = {"h": [num(x.get("value")) for x in home.get("linescores") or []],
                   "a": [num(x.get("value")) for x in away.get("linescores") or []]}
            if per["h"]:
                box = g.setdefault("box", {"src": "ESPN", "periods": {"a": [], "h": []}, "teams": {"a": {}, "h": {}}, "players": {"a": [], "h": []}})
                box["periods"] = per
        if lg == "ufc" and stype.get("state") == "post":
            g["winner"] = "home" if home.get("winner") else "away" if away.get("winner") else None
        # records
        for c, ab in ((home, h), (away, a)):
            if lg == "ufc": continue
            recs = c.get("records") or []
            td = T.setdefault(f"{lg}-{ab}", {})
            for r in recs:
                tp = (r.get("type") or r.get("name") or "").lower()
                if tp in ("total", "overall", "ytd"): td["rec"] = r.get("summary")
                elif tp == "home": td["homeRec"] = r.get("summary")
                elif tp in ("road", "away"): td["awayRec"] = r.get("summary")
            if c.get("form"): td["last5"] = c["form"]
        # odds: only update pre-game (freeze closing line once the game starts)
        if stype.get("state") == "pre":
            od = parse_odds(comp, g.get("odds"))
            if od:
                g["odds"] = od; g["oddsTs"] = NOWS
        # queue extra fetches
        sport_path = None
        if stype.get("state") == "post" and lg != "ufc":
            b = g.get("box") or {}
            if not (b.get("players", {}).get("h")) or not str(b.get("src", "")).startswith("ESPN summary"):
                if st > NOW - timedelta(days=4): FETCH_BOX.append(gid)
        if stype.get("state") == "pre" and st < NOW + timedelta(hours=40) and lg in ("nfl", "ncaaf", "nba", "ncaab", "nhl", "mlb", "soccer"):
            FETCH_WP.append(gid)
        touched[lg] += 1
        # season pack row
        if pack:
            pk = pack.replace("{mon}", MONTHS[int(et_date(start)[5:7]) - 1])
            sc = g.get("score") if status == "closed" else None
            pack_rows.setdefault(pk, []).append([et_date(start), a, sc["a"] if sc else None, h, sc["h"] if sc else None, start])
    return True

SPATH = {}
def scoreboards():
    days = [(NOW + timedelta(days=i)).strftime("%Y%m%d") for i in range(-3, 11)]
    for lg, srcs in LEAGUES.items():
        for path, q, title, pack in srcs:
            SPATH[lg] = SPATH.get(lg) or path
            if lg == "soccer" or lg == "ufc":
                # soccer/ufc: one ranged call
                rng = days[0] + "-" + days[-1]
                data = get(f"{SITE}{path}/scoreboard?dates={rng}&limit=300" + ("&" + q if q else ""))
                evs = (data or {}).get("events") or []
                for ev in evs:
                    try: do_event(lg, ev, title, pack)
                    except Exception as e: log("  ! event", lg, ev.get("id"), e)
                for ev in evs: SPATH.setdefault("ev:" + str(ev.get("id")), path)
                continue
            n = 0
            for d in days:
                data = get(f"{SITE}{path}/scoreboard?dates={d}" + ("&" + q if q else ""))
                if not data: continue
                if lg == "mlb" and not data.get("events") and d == days[0]:
                    pass
                for ev in data.get("events") or []:
                    try:
                        do_event(lg, ev, title, pack); n += 1
                        SPATH["ev:" + str(ev.get("id"))] = path
                    except Exception as e: log("  ! event", lg, ev.get("id"), e)
        log(f"{lg}: {touched[lg]} games updated")

# ---------------------------------------------------------------- summaries (box + win prob + starters)
NFL_GROUP = {"passing": {"YDS": "PASS YDS", "TD": "PASS TD", "AVG": "PASS AVG"},
             "rushing": {"YDS": "RUSH YDS", "TD": "RUSH TD", "AVG": "RUSH AVG", "LONG": "RUSH LONG"},
             "receiving": {"YDS": "REC YDS", "TD": "REC TD", "AVG": "REC AVG", "LONG": "REC LONG"}}

def summary(gid):
    g = G[gid]; eid = g.get("espnId")
    path = SPATH.get("ev:" + str(eid)) or SPATH.get(g["league"])
    if not eid or not path: return None
    return get(f"{SITE}{path}/summary?event={eid}")

def side_of(g, tm):
    lg = g["league"]
    ab = team_ab(lg, tm)
    return "h" if ab == g["home"] else "a" if ab == g["away"] else None

def do_box(gid):
    g = G[gid]; s = summary(gid)
    if not s: return
    bx = s.get("boxscore") or {}
    box = g.get("box") or {"periods": {"a": [], "h": []}}
    players = {"a": [], "h": []}; teams = {"a": {}, "h": {}}; starters = {"a": [], "h": []}
    for tp in bx.get("players") or []:
        sd = side_of(g, tp.get("team"))
        if not sd: continue
        seen = {}
        for grp in tp.get("statistics") or []:
            labels = grp.get("labels") or grp.get("names") or []
            gname = (grp.get("name") or "").lower()
            ren = NFL_GROUP.get(gname, {})
            for at in grp.get("athletes") or []:
                ath = at.get("athlete") or {}
                nm = ath.get("displayName")
                if not nm or at.get("didNotPlay"): continue
                stats = {}
                for lab, val in zip(labels, at.get("stats") or []):
                    if val in (None, "", "--"): continue
                    key = ren.get(lab, lab)
                    n = num(val)
                    stats[key] = n if n is not None and "/" not in str(val) and "-" not in str(val)[1:] else val
                if not stats: continue
                p = seen.get(nm)
                if not p:
                    p = seen[nm] = {"name": nm, "pos": (ath.get("position") or {}).get("abbreviation", ""), "stats": {}}
                    players[sd].append(p)
                    if at.get("starter"):
                        starters[sd].append({"pos": p["pos"], "player": nm, "num": ath.get("jersey", ""), "id": slug((g["home"] if sd == "h" else g["away"])) + "-" + slug(nm)})
                p["stats"].update(stats)
    for tt in bx.get("teams") or []:
        sd = side_of(g, tt.get("team"))
        if not sd: continue
        for st in tt.get("statistics") or []:
            lab = st.get("label") or st.get("name")
            if lab: teams[sd][lab] = st.get("displayValue")
    if players["h"] or players["a"]:
        box.update({"src": "ESPN summary (event " + str(g.get("espnId")) + ")", "players": players, "teams": teams})
        g["box"] = box
    # last-game starters -> projected lineup for NBA / soccer / MLB
    if g["league"] in ("nba", "soccer", "mlb"):
        for sd, ab in (("h", g["home"]), ("a", g["away"])):
            if len(starters[sd]) >= 5:
                td = T.setdefault(f"{g['league']}-{ab}", {})
                if str(td.get("lineupType", "")).startswith("Confirmed") and td.get("lineupTs", "") > ISO(NOW - timedelta(hours=12)): continue
                td["lineup"] = starters[sd]
                td["lineupType"] = "Projected lineup (last game's starters)"
                td["lineupSrc"] = "ESPN box score " + et_date(g["start"])
                td["lineupTs"] = NOWS

def do_wp(gid):
    g = G[gid]; s = summary(gid)
    if not s: return
    pr = s.get("predictor") or {}
    hp = num((pr.get("homeTeam") or {}).get("gameProjection"))
    ap = num((pr.get("awayTeam") or {}).get("gameProjection"))
    if hp is not None and ap is None: ap = round(100 - hp, 1)
    if hp is not None and 0 < hp < 100:
        g["wp"] = {"h": round(hp, 1), "a": round(ap, 1)}; g["wpSource"] = "ESPN Matchup Predictor (FPI/BPI)"
    # odds from pickcenter if scoreboard had none
    if not g.get("odds"):
        pc = s.get("pickcenter") or []
        if pc:
            o = pc[0]; od = {}
            if num(o.get("spread")) is not None: od["spread"] = num(o["spread"])
            if num(o.get("overUnder")) is not None: od["total"] = num(o["overUnder"])
            mh, ma = num((o.get("homeTeamOdds") or {}).get("moneyLine")), num((o.get("awayTeamOdds") or {}).get("moneyLine"))
            if mh is not None and ma is not None: od["mlH"], od["mlA"] = mh, ma
            if od:
                od["book"] = ((o.get("provider") or {}).get("name") or "ESPN") + " via ESPN"
                od["open"] = {k: od[k] for k in ("spread", "total", "mlH", "mlA") if k in od}; od["open"]["src"] = "first stored line"
                g["odds"] = od; g["oddsTs"] = NOWS
    # injuries from summary (covers college + soccer where no league injury feed)
    for ti in s.get("injuries") or []:
        sd = side_of(g, ti.get("team"))
        if not sd: continue
        lst = []
        for i in ti.get("injuries") or []:
            ath = i.get("athlete") or {}
            if not ath.get("displayName"): continue
            lst.append({"player": ath["displayName"], "pos": (ath.get("position") or {}).get("abbreviation", ""),
                        "status": i.get("status") or (i.get("type") or {}).get("description", ""),
                        "note": ((i.get("details") or {}).get("type") or i.get("shortComment") or "")[:160]})
        td = T.setdefault(f"{g['league']}-{g['home'] if sd == 'h' else g['away']}", {})
        if g["league"] not in INJ_LEAGUES:
            td["injuries"] = lst; td["injuriesTs"] = NOWS; td["injSrc"] = "ESPN game page injuries"

# ---------------------------------------------------------------- league injury reports
def injuries():
    for lg, path in INJ_LEAGUES.items():
        data = get(f"{SITE}{path}/injuries")
        if not data: continue
        n = 0
        for tm in data.get("injuries") or []:
            ab = team_ab(lg, {"id": tm.get("id"), "displayName": tm.get("displayName")})
            if not ab: continue
            lst = []
            for i in tm.get("injuries") or []:
                ath = i.get("athlete") or {}
                if not ath.get("displayName"): continue
                note = i.get("shortComment") or ""
                det = i.get("details") or {}
                if det.get("type"): note = (det.get("type") + (" (" + det.get("returnDate", "")[:10] + " est. return)" if det.get("returnDate") else "")) if not note else note
                lst.append({"player": ath["displayName"], "pos": (ath.get("position") or {}).get("abbreviation", ""),
                            "status": i.get("status") or det.get("fantasyStatus", {}).get("description", ""), "note": note[:200]})
            td = T.setdefault(f"{lg}-{ab}", {})
            td["injuries"] = lst; td["injuriesTs"] = NOWS; td["injSrc"] = "ESPN injury report"; n += 1
        # teams with no listed injuries: mark as clean (sourced)
        if n:
            for k, td in T.items():
                if k.startswith(lg + "-") and td.get("injuriesTs") != NOWS and lg in ("nfl", "nba", "nhl") and k[len(lg)+1:] in APPT.get(lg, {}):
                    td["injuries"] = []; td["injuriesTs"] = NOWS; td["injSrc"] = "ESPN injury report (none listed)"
        log(f"injuries {lg}: {n} teams")

# ---------------------------------------------------------------- rotating team detail (roster, schedule, news, depth)
def height(s):
    m = re.match(r"(\d+)'\s*(\d+)", s or "")
    return f"{m.group(1)}-{m.group(2)}" if m else (s or "")

def team_detail(lg, ab, eid):
    path = SPATH.get(lg) or LEAGUES[lg][0][0]
    td = T.setdefault(f"{lg}-{ab}", {})
    r = get(f"{SITE}{path}/teams/{eid}/roster")
    if r:
        aths = r.get("athletes") or []
        flat = []
        for x in aths:
            if isinstance(x, dict) and "items" in x: flat.extend(x["items"] or [])
            else: flat.append(x)
        ro = [{"id": ab.lower() + "-" + slug(p.get("displayName")), "name": p.get("displayName"), "num": str(p.get("jersey") or ""),
               "pos": (p.get("position") or {}).get("abbreviation", ""), "line": height(p.get("displayHeight"))} for p in flat if p.get("displayName")]
        if len(ro) >= 8:
            td["roster"] = ro; td["rosterSrc"] = "ESPN roster"; td["rosterTs"] = NOWS
    n = get(f"{SITE}{path}/news?team={eid}&limit=8")
    if n and n.get("articles"):
        art = []
        for a in n["articles"][:8]:
            url = ((a.get("links") or {}).get("web") or {}).get("href")
            if a.get("headline"): art.append({"title": a["headline"], "source": "ESPN", "date": (a.get("published") or "")[:10], "url": url})
        if art: td["news"] = art; td["newsTs"] = NOWS; td["newsSrc"] = "ESPN news"
    if lg in ("nfl", "ncaaf", "nba", "nhl", "mlb"):
        dc = get(f"{SITE}{path}/teams/{eid}/depthcharts")
        rows = []
        for chart in (dc or {}).get("depthchart") or (dc or {}).get("items") or []:
            for key, pos in (chart.get("positions") or {}).items():
                pl = [{"name": a.get("displayName"), "num": str(a.get("jersey") or ""), "id": ab.lower() + "-" + slug(a.get("displayName"))}
                      for a in (pos.get("athletes") or [])[:3] if a.get("displayName")]
                if pl: rows.append({"pos": ((pos.get("position") or {}).get("abbreviation") or key).upper(), "players": pl})
        stale = not td.get("depthTs") or td.get("depthTs") < ISO(NOW - timedelta(days=3))
        if len(rows) >= 4 and (stale or str(td.get("depthSrc", "")).startswith("ESPN")):
            td["depth"] = rows; td["depthSrc"] = "ESPN depth chart"; td["depthTs"] = NOWS
    td["detailTs"] = NOWS

def rotate_details(per_league=8):
    for lg in ("nfl", "nba", "nhl", "ncaaf", "ncaab", "soccer", "mlb"):
        ids = EMAP["ids"].get(lg) or {}
        if not ids: continue
        # prioritize teams that play in the next 2 days, then oldest detail
        soon = set()
        for g in G.values():
            if g.get("league") == lg and g.get("status") == "scheduled" and (parse_iso(g.get("start")) or NOW) < NOW + timedelta(days=2):
                soon.add(g["home"]); soon.add(g["away"])
        order = sorted(ids.items(), key=lambda kv: (kv[0] not in soon, T.get(f"{lg}-{kv[0]}", {}).get("detailTs", "")))
        lim = per_league * (2 if lg in ("ncaab", "ncaaf", "soccer") else 1)
        done = 0
        for ab, eid in order:
            if done >= lim: break
            if T.get(f"{lg}-{ab}", {}).get("detailTs", "") > ISO(NOW - timedelta(hours=20)): continue
            try: team_detail(lg, ab, eid); done += 1
            except Exception as e: log("  ! detail", lg, ab, e)
        log(f"details {lg}: {done} teams")

# ---------------------------------------------------------------- standings
def standings():
    for lg, path in (("nfl", "football/nfl"), ("nba", "basketball/nba"), ("nhl", "hockey/nhl")):
        data = get(f"https://site.api.espn.com/apis/v2/sports/{path}/standings")
        if not data: continue
        rows = []
        def walk(node, conf=None):
            nm = node.get("abbreviation") or node.get("name")
            if node.get("standings"):
                for e in node["standings"].get("entries") or []:
                    ab = team_ab(lg, e.get("team"))
                    st = {s.get("name"): s for s in e.get("stats") or []}
                    w = num((st.get("wins") or {}).get("value")); l = num((st.get("losses") or {}).get("value"))
                    row = {"ab": ab, "conf": conf or nm, "div": nm, "w": int(w or 0), "l": int(l or 0)}
                    for k in ("ties", "otLosses", "OTLosses"):
                        if k in st: row["otl" if "ot" in k.lower() else "t"] = int(num(st[k].get("value")) or 0)
                    if "playoffSeed" in st: row["rank"] = int(num(st["playoffSeed"].get("value")) or 0)
                    rows.append(row)
                    td = T.setdefault(f"{lg}-{ab}", {}); td["conf"] = conf or nm
                    if "streak" in st: td["streak"] = st["streak"].get("displayValue")
            for ch in node.get("children") or []:
                walk(ch, conf or (nm if node is not data else None))
        try:
            walk(data)
            if len(rows) >= 20:
                D["standings"][lg] = {"teams": rows, "src": "ESPN standings", "ts": NOWS}
                log(f"standings {lg}: {len(rows)}")
        except Exception as e: log("  ! standings", lg, e)

# ---------------------------------------------------------------- season packs
def merge_packs():
    for pk, rows in pack_rows.items():
        doc = D["seasons"].setdefault(pk, {"games": []})
        gl = doc.setdefault("games", [])
        idx = {}
        for i, r in enumerate(gl): idx[(r[1], r[3], r[0])] = i
        add = upd = 0
        for r in rows:
            key = None
            for off in (0, -1, 1):
                dd = (datetime.strptime(r[0], "%Y-%m-%d") + timedelta(days=off)).strftime("%Y-%m-%d")
                if (r[1], r[3], dd) in idx: key = (r[1], r[3], dd); break
            if key is None:
                gl.append(r); idx[(r[1], r[3], r[0])] = len(gl) - 1; add += 1
            else:
                old = gl[idx[key]]
                if r[2] is not None or old[5] != r[5]:
                    new = [old[0], r[1], r[2] if r[2] is not None else old[2], r[3], r[4] if r[4] is not None else old[4], r[5]]
                    if new != old: gl[idx[key]] = new; upd += 1
        gl.sort(key=lambda r: r[5] or r[0])
        doc["ts"] = NOWS
        if add or upd: log(f"season {pk}: +{add} new, {upd} updated")

# ---------------------------------------------------------------- prune
def prune():
    cut = ISO(NOW - timedelta(days=21))
    live_ids = {l.get("gameId") for l in D["leans"].values()}
    drop = [k for k, g in G.items() if (g.get("start") or "9") < cut and k not in live_ids]
    for k in drop: G.pop(k, None)
    if drop: log(f"pruned {len(drop)} old games")


# ---------------------------------------------------------------- public betting splits (Action Network public feed, best effort)
AN = {"nfl": "nfl", "ncaaf": "ncaaf", "nba": "nba", "ncaab": "ncaab", "nhl": "nhl", "mlb": "mlb", "soccer": "soccer"}
def splits():
    for lg, sp in AN.items():
        if not touched.get(lg): continue
        n = 0
        for off in (0, 1, 2):
            d = (NOW - timedelta(hours=5) + timedelta(days=off)).strftime("%Y%m%d")
            data = get(f"https://api.actionnetwork.com/web/v1/scoreboard/{sp}?period=game&bookIds=15,30,68,69&date={d}", tries=1)
            if not data: break
            for gm in data.get("games") or []:
                tm = {t.get("id"): t for t in gm.get("teams") or []}
                ht, at = tm.get(gm.get("home_team_id")), tm.get(gm.get("away_team_id"))
                if not ht or not at: continue
                if lg not in IDX: IDX[lg] = build_index(lg)
                def ab(t):
                    for k in (t.get("full_name"), t.get("display_name"), t.get("location") and t.get("location") + " " + (t.get("display_name") or "")):
                        if k and norm(k) in IDX[lg]: return IDX[lg][norm(k)]
                    a = (t.get("abbr") or "").upper(); a = ALIAS.get(lg, {}).get(a, a)
                    return a if f"{lg}-{a}" in T else None
                h, a = ab(ht), ab(at)
                st = parse_iso(gm.get("start_time"))
                if not h or not a or not st: continue
                gid = GIDX.get((lg, h, a, et_date(ISO(st))))
                if not gid:
                    gid = next((k for k, g in G.items() if g.get("league") == lg and g.get("home") == h and g.get("away") == a and abs(((parse_iso(g.get("start")) or NOW) - st).total_seconds()) < 36 * 3600), None)
                if not gid or gid not in G or G[gid].get("status") != "scheduled": continue
                best = None
                for o in gm.get("odds") or []:
                    if o.get("spread_home_public") is not None or o.get("ml_home_public") is not None or o.get("total_over_public") is not None:
                        best = o; break
                if not best: continue
                spl = {}
                for k, f in (("betsH", "spread_home_public"), ("handleH", "spread_home_money"), ("mlBetsH", "ml_home_public"),
                             ("mlHandleH", "ml_home_money"), ("betsO", "total_over_public"), ("handleO", "total_over_money")):
                    v = best.get(f)
                    if v is not None: spl[k] = round(float(v))
                if spl:
                    spl["source"] = "Action Network public betting %"; spl["ts"] = NOWS
                    G[gid]["splits"] = spl; n += 1
        log(f"splits {lg}: {n} games")

# ---------------------------------------------------------------- main
def main():
    t0 = time.time()
    steps = [("scoreboards", scoreboards), ("injuries", injuries), ("standings", standings), ("splits", splits)]
    for name, fn in steps:
        try: fn()
        except Exception as e: log("STEP FAILED", name, repr(e))
    log(f"box queue {len(FETCH_BOX)}, wp queue {len(FETCH_WP)}")
    for gid in FETCH_BOX[:80]:
        try: do_box(gid)
        except Exception as e: log("  ! box", gid, e)
    for gid in FETCH_WP[:140]:
        if time.time() - t0 > 12 * 60: break
        try: do_wp(gid)
        except Exception as e: log("  ! wp", gid, e)
    if time.time() - t0 < 14 * 60:
        try: rotate_details()
        except Exception as e: log("STEP FAILED details", repr(e))
    try: merge_packs()
    except Exception as e: log("STEP FAILED packs", repr(e))
    try: prune()
    except Exception as e: log("STEP FAILED prune", repr(e))
    total = sum(touched.values())
    ok = total > 0 and STATS["fail"] < STATS["calls"] * 0.5
    M["refresh"] = {"ok": ok, "ts": NOWS, "src": "GitHub Actions hourly (ESPN feeds)",
                    "note": f"Automatic hourly update: {total} games refreshed across " +
                            ", ".join(f"{k.upper()} {v}" for k, v in touched.items() if v) +
                            f"; {len(FETCH_BOX)} box scores, {len(FETCH_WP)} pregame model checks; {STATS['calls']} requests, {STATS['fail']} failed."}
    if total == 0:
        log("No games fetched - keeping previous data untouched except refresh note.")
    tmp = DATA + ".tmp"
    with open(tmp, "w") as f: json.dump(D, f, separators=(",", ":"), ensure_ascii=False)
    json.load(open(tmp))
    os.replace(tmp, DATA)
    log(f"done in {time.time() - t0:.0f}s; {STATS['calls']} calls, {STATS['fail']} failed")

if __name__ == "__main__":
    main()
