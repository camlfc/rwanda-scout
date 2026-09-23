#!/usr/bin/env python3
"""
fetch_form.py — run by GitHub Actions once per day.

Uses ScraperFC's botasaurus browser to call the Sofascore API.
botasaurus makes requests through a real headless Chrome session, so
Sofascore's datacenter-IP blocks don't apply.  No API key required.

Caching:
  scripts/team_ids.json   — club name → Sofascore team ID  (never re-fetched)
  scripts/lineups_cache.json — fixture_id → playerMap      (never re-fetched)
"""

import json
import time
import unicodedata
import re
from pathlib import Path
from urllib.parse import urlencode

# ScraperFC ships botasaurus; its internal util wraps get_json through Chrome.
# This bypasses Sofascore's datacenter-IP block that plain requests hits.
try:
    from ScraperFC.utils import botasaurus_browser_get_json  # type: ignore
    _USE_BROWSER = True
    print("✅  botasaurus browser available — using Chrome for Sofascore requests")
except ImportError:
    import requests as _requests
    _USE_BROWSER = False
    print("⚠️  botasaurus not available — falling back to plain requests (may get 403s)")

# ── Paths ─────────────────────────────────────────────────────────────────────

REPO_ROOT      = Path(__file__).parent.parent
OUTPUT_FILE    = REPO_ROOT / "player-form.json"
TEAM_IDS_FILE  = Path(__file__).parent / "team_ids.json"
LINEUPS_FILE   = Path(__file__).parent / "lineups_cache.json"

# ── Config ────────────────────────────────────────────────────────────────────

SS_BASE = "https://api.sofascore.com/api/v1"

SKIP_LEAGUES = {
    "Rwanda Premier League",
    "Algerian Ligue 1",
    "Luxembourg National Division",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

_SS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}

def ss_get(path: str, params: dict = None) -> dict | None:
    url = f"{SS_BASE}{path}"
    if params:
        url += "?" + urlencode(params)
    try:
        if _USE_BROWSER:
            return botasaurus_browser_get_json(url)
        else:
            r = _requests.get(url, headers=_SS_HEADERS, timeout=15)
            return r.json() if r.status_code == 200 else None
    except Exception as e:
        print(f"  ❌ {path}: {e}")
        return None


def norm_name(name: str) -> str:
    n = unicodedata.normalize("NFD", name.lower())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    n = re.sub(r"[^a-z0-9 ]", "", n).strip()
    return re.sub(r"\s+", " ", n)


def load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


# ── Team ID lookup (cached) ───────────────────────────────────────────────────

def get_team_id(club: str, team_ids: dict) -> int | None:
    if club in team_ids:
        return team_ids[club]

    data = ss_get("/search/all", {"q": club, "page": 0})
    if not data:
        return None

    team = next(
        (r for r in (data.get("results") or [])
         if r.get("type") == "team"
         and r.get("entity", {}).get("sport", {}).get("slug") == "football"),
        None,
    )
    if not team:
        print(f"  ⚠️  No football team found for '{club}'")
        return None

    team_id   = team["entity"]["id"]
    team_name = team["entity"].get("name", club)
    print(f"  ✅ Found: {team_name} (id={team_id})")
    team_ids[club] = team_id
    return team_id


# ── Lineup fetch (cached per fixture) ────────────────────────────────────────

def get_lineup(fixture_id: int, team_id: int, lineups_cache: dict) -> dict | None:
    key = str(fixture_id)
    if key in lineups_cache:
        cached = lineups_cache[key]
        return cached if cached else None

    lu_data = ss_get(f"/event/{fixture_id}/lineups")
    if not lu_data:
        lineups_cache[key] = None
        return None

    # Sofascore returns home/away objects
    # Figure out which side is our team
    home_id = (lu_data.get("home") or {}).get("team", {}).get("id")
    side = lu_data.get("home" if home_id == team_id else "away") or {}

    players = side.get("players") or []
    if not players:
        lineups_cache[key] = None
        return None

    player_map: dict[str, str] = {}
    for entry in players:
        p    = entry.get("player") or {}
        pid  = p.get("id")
        pnm  = norm_name(p.get("name", ""))
        mins = (entry.get("statistics") or {}).get("minutesPlayed", 0)

        if not entry.get("substitute"):
            status = "S"
        elif mins and mins > 0:
            status = "s"
        else:
            status = "B"

        if pnm: player_map[pnm]         = status
        if pid: player_map[f"id:{pid}"] = status

    lineups_cache[key] = player_map
    return player_map


# ── Per-club form fetch ───────────────────────────────────────────────────────

def fetch_club_form(club: str, team_ids: dict, lineups_cache: dict) -> dict | None:
    print(f"\n📋  {club}")

    team_id = get_team_id(club, team_ids)
    if not team_id:
        return None

    ev_data = ss_get(f"/team/{team_id}/events/last/0")
    if not ev_data:
        return None

    last5 = [
        e for e in (ev_data.get("events") or [])
        if e.get("status", {}).get("type") == "finished"
    ][-5:]

    if not last5:
        print(f"  ⚠️  No finished events for {club}")
        return None

    fixtures       = []
    has_any_lineup = False

    for ev in last5:
        ev_id   = ev["id"]
        is_home = ev.get("homeTeam", {}).get("id") == team_id
        gf      = ev.get("homeScore" if is_home else "awayScore", {}).get("current")
        ga      = ev.get("awayScore" if is_home else "homeScore", {}).get("current")
        result  = ("W" if gf > ga else "L" if gf < ga else "D") if (gf is not None and ga is not None) else "?"
        opp     = ev.get("awayTeam" if is_home else "homeTeam", {}).get("name", "?")
        ts      = ev.get("startTimestamp", 0)

        player_map = get_lineup(ev_id, team_id, lineups_cache) or {}
        if player_map:
            has_any_lineup = True

        fixtures.append({
            "fixtureId": ev_id,
            "date":      str(ts),
            "opp":       opp,
            "isHome":    is_home,
            "result":    result,
            "playerMap": player_map,
        })

        time.sleep(0.4)

    if not has_any_lineup:
        print(f"  ⚠️  No lineup data for {club}")
        return None

    print(f"  ✅ {len(fixtures)} fixtures stored for {club}")
    return {"teamId": team_id, "source": "sofascore", "fixtures": fixtures}


# ── Club list ─────────────────────────────────────────────────────────────────
# Zira FC is FIRST so Ange Mutsinzi gets form data on the very first run.

CLUBS = [
    # ── Priority clubs ────────────────────────────────────────────────────────
    ("Zira FC",                           "Azerbaijan Premier League"),
    ("Sabah FK",                          "Azerbaijan Premier League"),
    ("FK Karvan",                         "Azerbaijan Premier League"),
    ("KV Kortrijk",                       "Belgian First Division A"),
    ("Beerschot VA",                      "Belgian First Division A"),
    ("Bodo/Glimt",                        "Norwegian Eliteserien"),
    ("Malmo FF",                          "Swedish Allsvenskan"),
    ("BK Hacken",                         "Swedish Allsvenskan"),
    ("FC Groningen",                      "Dutch Eredivisie"),
    ("AEK Athens FC",                     "Greek Super League"),
    ("Kaizer Chiefs FC",                  "South African Premiership"),
    ("Al Hazem",                          "Saudi Pro League"),
    ("FUS Rabat",                         "Moroccan Botola Pro"),
    ("Hapoel Hadera",                     "Israeli Premier League"),
    ("CF Montreal",                       "MLS"),
    ("Olympique Beja",                    "Tunisian Ligue 1"),
    # ── Rest ──────────────────────────────────────────────────────────────────
    ("Al Ahli Tripoli",                   "Libyan Premier League"),
    ("Al-Suqoor",                         "Libyan Premier League"),
    ("Birmingham Legion",                 "USL Championship"),
    ("Rhode Island FC",                   "USL Championship"),
    ("AEL Limassol FC",                   "Cypriot First Division"),
    ("SL16 FC",                           "Belgian 1ste Nationale ACFF"),
    ("RAAL La Louviere",                  "Belgian 1ste Nationale ACFF"),
    ("KVC Wilrijk",                       "Belgian 1ste Nationale ACFF"),
    ("Ganshoren",                         "Belgian 1ste Nationale ACFF"),
    ("Thes Sport",                        "Belgian 1ste Nationale VV"),
    ("Juventus Next Gen",                 "Serie C"),
    ("FC La Chaux-de-Fonds",              "Swiss Promotion League"),
    ("Vitória Guimarães SC U19",          "Portuguese U19"),
    ("Eastern Suburbs FC (AUS)",          "Australian A-League"),
    ("PK-35",                             "Finnish Veikkausliiga"),
    ("Aalborg Freja IK",                  "Danish 2nd Division"),
    ("IK Brage",                          "Swedish Superettan"),
    ("IFK Luleå",                         "Swedish Superettan"),
    ("Burton Albion",                     "English League One"),
    ("Burnley U18",                       "English U18 Academy"),
    ("Sunderland U18",                    "English U18 Academy"),
    ("Arsenal U18",                       "English U18 Academy"),
    ("Storfors AIK",                      "Swedish lower"),
    ("Umeå FC",                           "Swedish lower"),
    ("Ljungskile SK",                     "Swedish lower"),
    ("Wacker München",                    "German Regionalliga"),
    ("La Louvière U21",                   "Belgian reserve"),
    ("Sporting Hasselt Youth",            "Belgian reserve"),
    ("PSV Youth",                         "Dutch reserve"),
    ("Veres Rivne U19",                   "Ukrainian U19"),
    ("Linares Unido",                     "Spanish lower"),
    ("Celta Academy B",                   "Spanish lower"),
    ("Fethiyespor",                       "Turkish lower"),
    ("Nairobi United",                    "Kenyan lower"),
    ("Vendee Poire-sur-Vie",              "French lower"),
    ("Maroons FC",                        "Qatari lower"),
    # ── SKIP (no Sofascore lineup data) ──────────────────────────────────────
    ("CS Constantine",                    "Algerian Ligue 1"),
    ("Mahembe FTC",                       "Rwanda Premier League"),
    ("Kiyovu Sports",                     "Rwanda Premier League"),
    ("APR FC",                            "Rwanda Premier League"),
    ("Rayon Sports",                      "Rwanda Premier League"),
    ("Police FC",                         "Rwanda Premier League"),
]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    team_ids      = load_json(TEAM_IDS_FILE)
    lineups_cache = load_json(LINEUPS_FILE)

    # Preserve clubs fetched in earlier runs
    existing_clubs: dict = {}
    if OUTPUT_FILE.exists():
        try:
            existing_clubs = json.loads(OUTPUT_FILE.read_text()).get("clubs", {})
        except Exception:
            pass

    result: dict = {}
    seen:   set  = set()

    for club, league in CLUBS:
        if club in seen:
            continue
        seen.add(club)

        if league in SKIP_LEAGUES:
            print(f"⏭️  Skipping {club} ({league})")
            result[club] = {"error": True}
            continue

        entry = fetch_club_form(club, team_ids, lineups_cache)
        if entry:
            result[club] = entry
        elif club in existing_clubs and not existing_clubs[club].get("error"):
            result[club] = existing_clubs[club]
            print(f"  ↩️  Using cached data for {club}")

        time.sleep(0.5)

    # ── Persist caches ────────────────────────────────────────────────────────
    TEAM_IDS_FILE.write_text(json.dumps(team_ids, indent=2))
    LINEUPS_FILE.write_text(json.dumps(lineups_cache, indent=2))

    OUTPUT_FILE.write_text(
        json.dumps({"ts": int(time.time() * 1000), "clubs": result}, indent=2)
    )

    clubs_with_data = sum(
        1 for v in result.values()
        if v and not v.get("error") and v.get("fixtures")
    )
    print(f"\n✅  Written {OUTPUT_FILE}  ({clubs_with_data} clubs with data)")


if __name__ == "__main__":
    main()
