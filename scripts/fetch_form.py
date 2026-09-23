#!/usr/bin/env python3
"""
fetch_form.py — run by GitHub Actions every 6 hours.

Calls the Sofascore API server-side (no CORS block from Python),
builds the playerFormCache JSON structure the PWA expects,
and writes it to player-form.json at the repo root.
"""

import json
import time
import unicodedata
import re
import requests
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

REPO_ROOT   = Path(__file__).parent.parent
OUTPUT_FILE = REPO_ROOT / "player-form.json"

# Leagues with no Sofascore data — skip immediately
SKIP_LEAGUES = {
    "Rwanda Premier League",
    "Algerian Ligue 1",
    "Luxembourg National Division",
}

# Sofascore API base
SS_BASE = "https://api.sofascore.com/api/v1"

# Headers that make the request look like a browser visiting sofascore.com
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.sofascore.com/",
    "Origin":          "https://www.sofascore.com",
    "Cache-Control":   "no-cache",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def ss_get(path: str, params: dict = None) -> dict | None:
    """GET from Sofascore API, returning parsed JSON or None on error."""
    url = f"{SS_BASE}{path}"
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if r.status_code == 200:
            return r.json()
        print(f"  ⚠️  {r.status_code} — {url}")
        return None
    except Exception as e:
        print(f"  ❌ Request error: {e}")
        return None


def norm_name(name: str) -> str:
    """Normalise a player name for the playerMap key (matches PWA logic)."""
    n = unicodedata.normalize("NFD", name.lower())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    n = re.sub(r"[^a-z0-9 ]", "", n).strip()
    return re.sub(r"\s+", " ", n)


# ── Core fetch ────────────────────────────────────────────────────────────────

def fetch_club_form(club: str) -> dict | None:
    """
    Returns a cache entry:
      { teamId, source:'sofascore', fixtures:[{fixtureId,date,opp,isHome,result,playerMap}] }
    or None if the club can't be found / has no lineup data.
    """
    print(f"  🔍 Searching: {club}")

    # 1. Find the team on Sofascore
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

    # 2. Last page of finished events
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

    # 3. Lineups per fixture
    fixtures     = []
    has_any_data = False

    for ev in last5:
        ev_id   = ev["id"]
        is_home = ev.get("homeTeam", {}).get("id") == team_id
        gf      = ev.get("homeScore" if is_home else "awayScore", {}).get("current")
        ga      = ev.get("awayScore" if is_home else "homeScore", {}).get("current")
        result  = ("W" if gf > ga else "L" if gf < ga else "D") if (gf is not None and ga is not None) else "?"
        opp     = ev.get("awayTeam" if is_home else "homeTeam", {}).get("name", "?")
        ts      = ev.get("startTimestamp", 0)
        player_map = {}

        lu_data = ss_get(f"/event/{ev_id}/lineups")
        if lu_data:
            side = lu_data.get("home" if is_home else "away", {})
            players = side.get("players") or []
            if players:
                has_any_data = True
                for entry in players:
                    p     = entry.get("player") or {}
                    pid   = p.get("id")
                    pname = norm_name(p.get("name", ""))
                    if not pname and not pid:
                        continue
                    mins = (entry.get("statistics") or {}).get("minutesPlayed", 0)
                    if not entry.get("substitute"):
                        status = "S"          # started
                    elif mins and mins > 0:
                        status = "s"          # came on
                    else:
                        status = "B"          # on bench, unused
                    if pname:
                        player_map[pname]          = status
                    if pid:
                        player_map[f"id:{pid}"]    = status

        fixtures.append({
            "fixtureId": ev_id,
            "date":      f"{ts}",   # epoch seconds (PWA converts)
            "opp":       opp,
            "isHome":    is_home,
            "result":    result,
            "playerMap": player_map,
        })

        time.sleep(0.3)  # gentle pacing

    if not has_any_data:
        print(f"  ⚠️  No lineup data for {club}")
        return None

    print(f"  ✅ Stored {len(fixtures)} fixtures for {club}")
    return {"teamId": team_id, "source": "sofascore", "fixtures": fixtures}


# ── Club list ─────────────────────────────────────────────────────────────────
# Extracted from the PLAYERS / NA_PLAYERS arrays in index.html.
# Update this list whenever you add a new player to the Google Sheet.
# Clubs in SKIP_LEAGUES are filtered out below — no need to remove them here.

CLUBS = [
    ("Zira FC",                           "Azerbaijan Premier League"),
    ("Al Ahli Tripoli",                   "Libyan Premier League"),
    ("Birmingham Legion",                 "USL Championship"),
    ("AEL Limassol FC",                   "Cypriot First Division"),
    ("SL16 FC",                           "Belgian 1ste Nationale ACFF"),
    ("Malmo FF",                          "Swedish Allsvenskan"),
    ("Sabah FK",                          "Azerbaijan Premier League"),
    ("CF Montreal",                       "MLS"),
    ("Juventus Next Gen",                 "Serie C"),
    ("KV Kortrijk",                       "Belgian First Division A"),
    ("Beerschot VA",                      "Belgian First Division A"),
    ("Bodo/Glimt",                        "Norwegian Eliteserien"),
    ("Thes Sport",                        "Belgian 1ste Nationale VV"),
    ("FC Groningen",                      "Dutch Eredivisie"),
    ("BK Hacken",                         "Swedish Allsvenskan"),
    ("Burnley U18",                       "English U18 Academy"),
    ("Al Hazem",                          "Saudi Pro League"),
    ("CS Constantine",                    "Algerian Ligue 1"),
    ("RAAL La Louviere",                  "Belgian 1ste Nationale ACFF"),
    ("AEK Athens FC",                     "Greek Super League"),
    ("FC La Chaux-de-Fonds",              "Swiss Promotion League"),
    ("Vitória Guimarães SC U19",          "Portuguese U19"),
    ("Eastern Suburbs FC (AUS)",          "Australian A-League"),
    ("PK-35",                             "Finnish Veikkausliiga"),
    ("Aalborg Freja IK",                  "Danish 2nd Division"),
    ("IK Brage",                          "Swedish Superettan"),
    ("Burton Albion",                     "English League One"),
    ("FK Karvan",                         "Azerbaijan Premier League"),
    ("KVC Wilrijk",                       "Belgian 1ste Nationale ACFF"),
    ("Ganshoren",                         "Belgian 1ste Nationale ACFF"),
    ("Rhode Island FC",                   "USL Championship"),
    ("IFK Luleå",                         "Swedish Superettan"),
    ("Storfors AIK",                      "Swedish lower"),
    ("Wacker München",                    "German Regionalliga"),
    ("La Louvière U21",                   "Belgian reserve"),
    ("Sunderland U18",                    "English U18 Academy"),
    ("Arsenal U18",                       "English U18 Academy"),
    ("Veres Rivne U19",                   "Ukrainian U19"),
    ("Linares Unido",                     "Spanish lower"),
    ("Fethiyespor",                       "Turkish lower"),
    ("Nairobi United",                    "Kenyan lower"),
    ("Umeå FC",                           "Swedish lower"),
    ("Kaizer Chiefs FC",                  "South African Premiership"),
    ("Hapoel Hadera",                     "Israeli Premier League"),
    ("Ljungskile SK",                     "Swedish lower"),
    ("FUS Rabat",                         "Moroccan Botola Pro"),
    ("Al-Suqoor",                         "Libyan Premier League"),
    ("Olympique Beja",                    "Tunisian Ligue 1"),
    ("Vendee Poire-sur-Vie",              "French lower"),
    ("Celta Academy B",                   "Spanish lower"),
    ("Thes Sport",                        "Belgian 1ste Nationale VV"),
    ("Sporting Hasselt Youth",            "Belgian reserve"),
    ("PSV Youth",                         "Dutch reserve"),
    ("Maroons FC",                        "Qatari lower"),
    ("Mahembe FTC",                       "Rwanda Premier League"),
    ("Kiyovu Sports",                     "Rwanda Premier League"),
    ("APR FC",                            "Rwanda Premier League"),
    ("Rayon Sports",                      "Rwanda Premier League"),
    ("Police FC",                         "Rwanda Premier League"),
]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    result = {}
    seen   = set()

    for club, league in CLUBS:
        if club in seen:
            continue
        seen.add(club)

        if league in SKIP_LEAGUES:
            print(f"⏭️  Skipping {club} ({league})")
            result[club] = {"error": True}
            continue

        print(f"\n📋 {club} [{league}]")
        entry = fetch_club_form(club)
        if entry:
            result[club] = entry
        else:
            # Leave undefined so the PWA retries on next deploy (not stored as error)
            pass

        time.sleep(0.5)

    OUTPUT_FILE.write_text(json.dumps({"ts": int(time.time() * 1000), "clubs": result}, indent=2))
    print(f"\n✅ Written {OUTPUT_FILE} ({len(result)} clubs)")


if __name__ == "__main__":
    main()
