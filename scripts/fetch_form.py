#!/usr/bin/env python3
"""
fetch_form.py — fetches club form + lineup data from API-Football.

API-Football works from residential IPs and has full lineup data.
Free tier: 100 requests/day.  Caching means subsequent runs are cheap.

Caching:
  scripts/team_ids.json      — club name -> API-Football team ID  (never re-fetched)
  scripts/lineups_cache.json — fixture_id -> playerMap            (never re-fetched)
"""

import json
import os
import time
import unicodedata
import re
from pathlib import Path
from urllib.parse import urlencode
import requests

REPO_ROOT      = Path(__file__).parent.parent
OUTPUT_FILE    = REPO_ROOT / "player-form.json"
TEAM_IDS_FILE  = Path(__file__).parent / "team_ids.json"
LINEUPS_FILE   = Path(__file__).parent / "lineups_cache.json"

API_BASE = "https://v3.football.api-sports.io"
API_KEY  = os.environ.get("APIFOOTBALL_KEY", "604d0a4e44ea0333c4695e9095e73688")

SKIP_LEAGUES = {
    "Rwanda Premier League",
    "Algerian Ligue 1",
    "Luxembourg National Division",
}

_HEADERS = {
    "x-apisports-key": API_KEY,
}


def api_get(path, params=None):
    url = f"{API_BASE}{path}"
    if params:
        url += "?" + urlencode(params)
    try:
        r = requests.get(url, headers=_HEADERS, timeout=20)
        if r.status_code == 429:
            print("  rate limited — sleeping 60s")
            time.sleep(60)
            r = requests.get(url, headers=_HEADERS, timeout=20)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        print(f"  ERR {path}: {e}")
        return None


def norm_name(name):
    n = unicodedata.normalize("NFD", name.lower())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    n = re.sub(r"[^a-z0-9 ]", "", n).strip()
    return re.sub(r"\s+", " ", n)


def load_json(path):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


def get_team_id(club, team_ids):
    if club in team_ids:
        return team_ids[club]
    data = api_get("/teams", {"search": club})
    if not data or not data.get("response"):
        print(f"  no team found for '{club}'")
        return None
    # Pick best match by normalised name
    cn = norm_name(club)
    resp = data["response"]
    match = next(
        (r for r in resp if norm_name(r["team"]["name"]) == cn),
        resp[0]  # fallback: first result
    )
    team_id   = match["team"]["id"]
    team_name = match["team"]["name"]
    print(f"  found: {team_name} (id={team_id})")
    team_ids[club] = team_id
    return team_id


def get_lineup(fixture_id, team_id, lineups_cache):
    key = str(fixture_id)
    if key in lineups_cache:
        cached = lineups_cache[key]
        return cached if cached else None

    data = api_get("/fixtures/lineups", {"fixture": fixture_id})
    if not data or not data.get("response"):
        lineups_cache[key] = None
        return None

    # Find our team's lineup
    side = next(
        (r for r in data["response"] if r["team"]["id"] == team_id),
        None
    )
    if not side:
        lineups_cache[key] = None
        return None

    player_map = {}
    for entry in side.get("startXI") or []:
        p = entry.get("player") or {}
        pid  = p.get("id")
        pnm  = norm_name(p.get("name", ""))
        if pnm: player_map[pnm]         = "S"
        if pid: player_map[f"id:{pid}"] = "S"

    for entry in side.get("substitutes") or []:
        p = entry.get("player") or {}
        pid  = p.get("id")
        pnm  = norm_name(p.get("name", ""))
        # Mark as bench (B); we'd need events to know if they came on
        if pnm: player_map[pnm]         = "B"
        if pid: player_map[f"id:{pid}"] = "B"

    lineups_cache[key] = player_map if player_map else None
    return player_map if player_map else None


def fetch_club_form(club, team_ids, lineups_cache):
    print(f"\n{club}")
    team_id = get_team_id(club, team_ids)
    if not team_id:
        return None

    data = api_get("/fixtures", {"team": team_id, "last": 5})
    if not data or not data.get("response"):
        print(f"  no fixtures found")
        return None

    fixtures_raw = data["response"]
    if not fixtures_raw:
        print(f"  no finished fixtures")
        return None

    fixtures = []
    has_any_lineup = False

    for fx in fixtures_raw:
        fix_id  = fx["fixture"]["id"]
        is_home = fx["teams"]["home"]["id"] == team_id
        gf = fx["goals"]["home"] if is_home else fx["goals"]["away"]
        ga = fx["goals"]["away"] if is_home else fx["goals"]["home"]

        if gf is not None and ga is not None:
            result = "W" if gf > ga else ("L" if gf < ga else "D")
        else:
            result = "?"

        opp = fx["teams"]["away"]["name"] if is_home else fx["teams"]["home"]["name"]
        ts  = fx["fixture"].get("timestamp", 0)

        player_map = get_lineup(fix_id, team_id, lineups_cache) or {}
        if player_map:
            has_any_lineup = True

        fixtures.append({
            "fixtureId": fix_id,
            "date":      str(ts),
            "opp":       opp,
            "isHome":    is_home,
            "result":    result,
            "playerMap": player_map,
        })
        time.sleep(0.5)

    if not fixtures:
        return None

    if not has_any_lineup:
        print(f"  no lineup data (may not be available yet)")

    print(f"  stored {len(fixtures)} fixtures")
    return {"teamId": team_id, "source": "api-football", "fixtures": fixtures}


CLUBS = [
    ("Zira FC",                          "Azerbaijan Premier League"),
    ("Sabah FK",                         "Azerbaijan Premier League"),
    ("FK Karvan",                        "Azerbaijan Premier League"),
    ("KV Kortrijk",                      "Belgian First Division A"),
    ("Beerschot VA",                     "Belgian First Division A"),
    ("Bodo/Glimt",                       "Norwegian Eliteserien"),
    ("Malmo FF",                         "Swedish Allsvenskan"),
    ("BK Hacken",                        "Swedish Allsvenskan"),
    ("FC Groningen",                     "Dutch Eredivisie"),
    ("AEK Athens FC",                    "Greek Super League"),
    ("Kaizer Chiefs FC",                 "South African Premiership"),
    ("Al Hazem",                         "Saudi Pro League"),
    ("FUS Rabat",                        "Moroccan Botola Pro"),
    ("Hapoel Hadera",                    "Israeli Premier League"),
    ("CF Montreal",                      "MLS"),
    ("Olympique Beja",                   "Tunisian Ligue 1"),
    ("Al Ahli Tripoli",                  "Libyan Premier League"),
    ("Al-Suqoor",                        "Libyan Premier League"),
    ("Birmingham Legion",                "USL Championship"),
    ("Rhode Island FC",                  "USL Championship"),
    ("AEL Limassol FC",                  "Cypriot First Division"),
    ("SL16 FC",                          "Belgian 1ste Nationale ACFF"),
    ("RAAL La Louviere",                 "Belgian 1ste Nationale ACFF"),
    ("KVC Wilrijk",                      "Belgian 1ste Nationale ACFF"),
    ("Ganshoren",                        "Belgian 1ste Nationale ACFF"),
    ("Thes Sport",                       "Belgian 1ste Nationale VV"),
    ("Juventus Next Gen",                "Serie C"),
    ("FC La Chaux-de-Fonds",             "Swiss Promotion League"),
    ("Vitoria Guimaraes SC U19",         "Portuguese U19"),
    ("Eastern Suburbs FC (AUS)",         "Australian A-League"),
    ("PK-35",                            "Finnish Veikkausliiga"),
    ("Aalborg Freja IK",                 "Danish 2nd Division"),
    ("IK Brage",                         "Swedish Superettan"),
    ("IFK Lulea",                        "Swedish Superettan"),
    ("Burton Albion",                    "English League One"),
    ("Burnley U18",                      "English U18 Academy"),
    ("Sunderland U18",                   "English U18 Academy"),
    ("Arsenal U18",                      "English U18 Academy"),
    ("Storfors AIK",                     "Swedish lower"),
    ("Umea FC",                          "Swedish lower"),
    ("Ljungskile SK",                    "Swedish lower"),
    ("Wacker Munchen",                   "German Regionalliga"),
    ("La Louviere U21",                  "Belgian reserve"),
    ("Sporting Hasselt Youth",           "Belgian reserve"),
    ("PSV Youth",                        "Dutch reserve"),
    ("Veres Rivne U19",                  "Ukrainian U19"),
    ("Linares Unido",                    "Spanish lower"),
    ("Celta Academy B",                  "Spanish lower"),
    ("Fethiyespor",                      "Turkish lower"),
    ("Nairobi United",                   "Kenyan lower"),
    ("Vendee Poire-sur-Vie",             "French lower"),
    ("Maroons FC",                       "Qatari lower"),
    ("CS Constantine",                   "Algerian Ligue 1"),
    ("Mahembe FTC",                      "Rwanda Premier League"),
    ("Kiyovu Sports",                    "Rwanda Premier League"),
    ("APR FC",                           "Rwanda Premier League"),
    ("Rayon Sports",                     "Rwanda Premier League"),
    ("Police FC",                        "Rwanda Premier League"),
]


def main():
    team_ids      = load_json(TEAM_IDS_FILE)
    lineups_cache = load_json(LINEUPS_FILE)

    existing_clubs = {}
    if OUTPUT_FILE.exists():
        try:
            existing_clubs = json.loads(OUTPUT_FILE.read_text()).get("clubs", {})
        except Exception:
            pass

    result = {}
    seen   = set()

    for club, league in CLUBS:
        if club in seen:
            continue
        seen.add(club)

        if league in SKIP_LEAGUES:
            print(f"skipping {club} ({league})")
            result[club] = {"error": True}
            continue

        entry = fetch_club_form(club, team_ids, lineups_cache)
        if entry:
            result[club] = entry
        elif club in existing_clubs and not existing_clubs[club].get("error"):
            result[club] = existing_clubs[club]
            print(f"  using cached data")

        time.sleep(0.5)

    TEAM_IDS_FILE.write_text(json.dumps(team_ids, indent=2))
    LINEUPS_FILE.write_text(json.dumps(lineups_cache, indent=2))
    OUTPUT_FILE.write_text(
        json.dumps({"ts": int(time.time() * 1000), "clubs": result}, indent=2)
    )

    clubs_with_data = sum(
        1 for v in result.values()
        if v and not v.get("error") and v.get("fixtures")
    )
    print(f"\nDone: {clubs_with_data} clubs with data")


if __name__ == "__main__":
    main()
