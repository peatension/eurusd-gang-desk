     """
BET SQUAD — data-first Telegram betting research bot.
No fabricated fixtures, odds, probabilities or fallback selections.
SportyBet is the manual execution venue; comparison odds are not SportyBet prices.
"""
from __future__ import annotations

import os, time, json, math, sqlite3, logging, argparse
from datetime import datetime, timedelta, timezone
from typing import Any
import requests

API_KEY = os.getenv("BET_API", "").strip()
BOT_TOKEN = os.getenv("BET_BOT_KEY", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
DB_PATH = os.getenv("BET_DB_PATH", "bet_squad.sqlite3")
FOOTBALL_BASE = "https://v3.football.api-sports.io"
BOOKMAKER_ID = int(os.getenv("BET_BOOKMAKER_ID", "7"))  # William Hill, confirmed by user's API test
BOOKMAKER_NAME = os.getenv("BET_BOOKMAKER_NAME", "William Hill")
TIMEOUT = 25
MIN_EDGE = float(os.getenv("BET_MIN_EDGE", "0.04"))
MAX_SELECTIONS = int(os.getenv("BET_MAX_SELECTIONS", "10"))
SUPPORTED_SPORTS = ["football"]  # Other sports are intentionally disabled until their API coverage is verified.

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bet-squad")


class ProviderError(RuntimeError): pass


class ApiFootball:
    def __init__(self, key: str):
        if not key:
            raise ProviderError("BET_API is missing.")
        self.session = requests.Session()
        self.session.headers.update({"x-apisports-key": key})

    def get(self, endpoint: str, **params) -> list[dict]:
        url = f"{FOOTBALL_BASE}/{endpoint.lstrip('/')}"
        try:
            response = self.session.get(url, params=params, timeout=TIMEOUT)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ProviderError(f"API request failed: {exc}") from exc
        errors = payload.get("errors")
        if errors:
            raise ProviderError(f"API-Sports returned errors: {errors}")
        return payload.get("response") or []

    def fixtures(self, date_str: str) -> list[dict]:
        return self.get("fixtures", date=date_str, timezone="Africa/Lagos")

    def recent_team_fixtures(self, team_id: int, before_date: str, limit: int = 10) -> list[dict]:
        rows = self.get("fixtures", team=team_id, last=limit, to=before_date,
                        status="FT", timezone="Africa/Lagos")
        return [r for r in rows if r.get("fixture", {}).get("status", {}).get("short") in ("FT", "AET", "PEN")]

    def fixture_odds(self, fixture_id: int) -> list[dict]:
        return self.get("odds", fixture=fixture_id, bookmaker=BOOKMAKER_ID)

    def fixture_statistics(self, fixture_id: int) -> list[dict]:
        return self.get("fixtures/statistics", fixture=fixture_id)

    def bookmaker_catalogue(self) -> list[dict]:
        return self.get("odds/bookmakers", id=BOOKMAKER_ID)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS subscribers(
        chat_id TEXT PRIMARY KEY, joined_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS scans(
        id INTEGER PRIMARY KEY AUTOINCREMENT, scanned_at TEXT NOT NULL,
        fixture_id INTEGER NOT NULL, sport TEXT NOT NULL, fixture TEXT NOT NULL,
        market TEXT NOT NULL, selection TEXT NOT NULL, odds REAL NOT NULL,
        model_probability REAL NOT NULL, implied_probability REAL NOT NULL,
        edge REAL NOT NULL, bookmaker TEXT NOT NULL, kickoff TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN',
        UNIQUE(fixture_id, market, selection, scanned_at))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS telegram_updates(
        update_id INTEGER PRIMARY KEY)""")
    conn.commit()
    return conn


def poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def score_matrix(home_lambda: float, away_lambda: float, max_goals: int = 10):
    hp = [poisson_pmf(i, home_lambda) for i in range(max_goals + 1)]
    ap = [poisson_pmf(i, away_lambda) for i in range(max_goals + 1)]
    matrix = [[h * a for a in ap] for h in hp]
    total = sum(map(sum, matrix))
    return [[v / total for v in row] for row in matrix]


def market_probabilities(matrix):
    n = len(matrix)
    home = draw = away = over25 = under25 = btts_yes = btts_no = 0.0
    for h in range(n):
        for a in range(n):
            p = matrix[h][a]
            if h > a: home += p
            elif h == a: draw += p
            else: away += p
            if h + a >= 3: over25 += p
            else: under25 += p
            if h > 0 and a > 0: btts_yes += p
            else: btts_no += p
    return {
        "match winner": {"home": home, "draw": draw, "away": away},
        "1x2": {"home": home, "draw": draw, "away": away},
        "over/under": {"over 2.5": over25, "under 2.5": under25},
        "both teams score": {"yes": btts_yes, "no": btts_no},
        "btts": {"yes": btts_yes, "no": btts_no},
        "goals over/under": {"over 2.5": over25, "under 2.5": under25},
    }


def team_goal_rates(api: ApiFootball, team_id: int, before_date: str):
    rows = api.recent_team_fixtures(team_id, before_date, 10)
    scored, conceded, count = 0, 0, 0
    for row in rows:
        teams, goals = row.get("teams", {}), row.get("goals", {})
        is_home = teams.get("home", {}).get("id") == team_id
        own = goals.get("home" if is_home else "away")
        opp = goals.get("away" if is_home else "home")
        if isinstance(own, int) and isinstance(opp, int):
            scored += own
            conceded += opp
            count += 1
    if count < 5:
        return None
    return {"scored": scored / count, "conceded": conceded / count, "matches": count}


def estimate_lambdas(home_rates, away_rates):
    # Blended attack/defence rates; league average unavailable, so this is a conservative
    # team-form approximation, not a fully calibrated league-adjusted model.
    h = (home_rates["scored"] + away_rates["conceded"]) / 2
    a = (away_rates["scored"] + home_rates["conceded"]) / 2
    return max(0.15, min(4.0, h)), max(0.15, min(4.0, a))


def normalize_market_name(name: str) -> str:
    return " ".join(str(name).lower().replace("-", " ").replace("_", " ").split())


def parse_selection(value: str):
    v = str(value).strip().lower()
    aliases = {
        "home": ("match winner", "home"), "1": ("match winner", "home"),
        "draw": ("match winner", "draw"), "x": ("match winner", "draw"),
        "away": ("match winner", "away"), "2": ("match winner", "away"),
        "over 2.5": ("over/under", "over 2.5"),
        "under 2.5": ("over/under", "under 2.5"),
        "yes": ("both teams score", "yes"), "no": ("both teams score", "no"),
    }
    return aliases.get(v)


def extract_odds(odds_payload):
    found = []
    for fixture_block in odds_payload:
        for bookmaker in fixture_block.get("bookmakers", []):
            if bookmaker.get("id") != BOOKMAKER_ID:
                continue
            for bet in bookmaker.get("bets", []):
                market_name = normalize_market_name(bet.get("name", ""))
                mapped = None
                if market_name in ("match winner", "1x2", "winner"):
                    mapped = "match winner"
                elif market_name in ("over under", "goals over under", "total goals"):
                    mapped = "over/under"
                elif market_name in ("both teams score", "btts"):
                    mapped = "both teams score"
                if not mapped:
                    continue
                for value in bet.get("values", []):
                    label = normalize_market_name(value.get("value", ""))
                    odds = value.get("odd")
                    try: price = float(odds)
                    except (TypeError, ValueError): continue
                    if price > 1.0 and label:
                        found.append((mapped, label, price))
    return found


def analyse_fixture(api: ApiFootball, fixture: dict):
    fx = fixture.get("fixture", {})
    teams = fixture.get("teams", {})
    league = fixture.get("league", {})
    home, away = teams.get("home", {}), teams.get("away", {})
    if not all((fx.get("id"), home.get("id"), away.get("id"))):
        return []
    kickoff = fx.get("date", "")
    before = kickoff[:10]
    hr = team_goal_rates(api, home["id"], before)
    ar = team_goal_rates(api, away["id"], before)
    if not hr or not ar:
        log.info("Skip %s: fewer than five completed recent matches for one team", fx.get("id"))
        return []
    hl, al = estimate_lambdas(hr, ar)
    probs = market_probabilities(score_matrix(hl, al))
    try:
        odds_data = extract_odds(api.fixture_odds(fx["id"]))
    except ProviderError as exc:
        log.warning("Odds unavailable for fixture %s: %s", fx["id"], exc)
        return []
    candidates = []
    # Remove the bookmaker margin within each returned market before comparing
    # the model probability with the market's normalized implied probability.
    grouped = {}
    for market, selection, price in odds_data:
        grouped.setdefault(market, {})[selection] = price
    for market, outcomes in grouped.items():
        reciprocal_sum = sum(1.0 / price for price in outcomes.values())
        if reciprocal_sum <= 0:
            continue
        for selection, price in outcomes.items():
            market_probs = probs.get(market, {})
            # Provider labels must map exactly to a supported model outcome.
            p = market_probs.get(selection)
            if p is None:
                continue
            raw_implied = 1.0 / price
            implied = raw_implied / reciprocal_sum
            edge = p - implied
            if edge >= MIN_EDGE:
                candidates.append({
                "fixture_id": fx["id"], "sport": "Football",
                "fixture": f'{home.get("name")} vs {away.get("name")}',
                "league": league.get("name", "Unknown league"),
                "market": market, "selection": selection, "odds": price,
                "probability": p, "implied": implied, "edge": edge,
                "bookmaker": BOOKMAKER_NAME, "kickoff": kickoff,
                "home_matches": hr["matches"], "away_matches": ar["matches"],
                "lambda_home": hl, "lambda_away": al,
                "raw_implied": raw_implied
            })
    return candidates


def scan_today():
    api = ApiFootball(API_KEY)
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    fixtures = api.fixtures(today)
    picks = []
    for fixture in fixtures:
        if fixture.get("fixture", {}).get("status", {}).get("short") != "NS":
            continue
        try:
            picks.extend(analyse_fixture(api, fixture))
        except ProviderError as exc:
            log.warning("Skipping fixture due to provider error: %s", exc)
    # Highest edge first; only one market per fixture to reduce correlated legs.
    picks.sort(key=lambda x: x["edge"], reverse=True)
    selected, used = [], set()
    for p in picks:
        if p["fixture_id"] in used:
            continue
        selected.append(p); used.add(p["fixture_id"])
        if len(selected) >= MAX_SELECTIONS: break
    conn = db()
    now = datetime.now(timezone.utc).isoformat()
    for p in selected:
        conn.execute("""INSERT OR IGNORE INTO scans
          (scanned_at,fixture_id,sport,fixture,market,selection,odds,model_probability,
           implied_probability,edge,bookmaker,kickoff)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
          (now,p["fixture_id"],p["sport"],p["fixture"],p["market"],p["selection"],
           p["odds"],p["probability"],p["implied"],p["edge"],p["bookmaker"],p["kickoff"]))
    conn.commit(); conn.close()
    return selected, len(fixtures)


def fmt_report(picks, fixture_count):
    if not picks:
        return ("🛡️ *BET SQUAD — NO BET SLATE*\n\n"
                f"Scanned {fixture_count} fixtures. No selection passed the current data and edge filters.\n"
                "No odds or predictions were invented. Try again when more bookmaker data is available.")
    lines = [f"🧠 *BET SQUAD — DATA SCREEN*", f"Fixtures checked: {fixture_count}",
             f"Qualified candidates: {len(picks)}", ""]
    for i,p in enumerate(picks,1):
        lines.append(
            f"*{i}. {p['fixture']}*\n"
            f"🏆 {p['league']} | {p['kickoff']}\n"
            f"Market: {p['market']} — *{p['selection']}*\n"
            f"Comparison odds ({p['bookmaker']}): *{p['odds']:.2f}*\n"
            f"Model probability: {p['probability']:.1%} | Implied: {p['implied']:.1%}\n"
            f"Estimated edge: {p['edge']:.1%}\n"
            f"Form sample: {p['home_matches']} home-team games / {p['away_matches']} away-team games\n"
        )
    lines.append("⚠️ Comparison odds are NOT SportyBet odds. Confirm the same fixture, market, selection and live price on SportyBet before any manual bet. Probabilities are estimates, not guarantees.")
    return "\n".join(lines)


def tg_call(method, payload=None):
    if not BOT_TOKEN: raise RuntimeError("BET_BOT_KEY is missing.")
    r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
                      json=payload or {}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"): raise RuntimeError(str(data))
    return data["result"]


def send_message(chat_id, text):
    for start in range(0, len(text), 3900):
        tg_call("sendMessage", {"chat_id": chat_id, "text": text[start:start+3900],
                                "parse_mode": "Markdown", "disable_web_page_preview": True})


def subscribe(chat_id):
    conn = db()
    conn.execute("INSERT OR IGNORE INTO subscribers(chat_id,joined_at) VALUES(?,?)",
                 (str(chat_id), datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()


def subscriber_ids():
    conn = db()
    ids = [r["chat_id"] for r in conn.execute("SELECT chat_id FROM subscribers")]
    conn.close()
    return ids


def is_admin(chat_id):
    return bool(ADMIN_ID) and str(chat_id) == str(ADMIN_ID)


def handle_update(update):
    msg = update.get("message") or update.get("edited_message")
    if not msg or not msg.get("text"): return
    chat_id = msg["chat"]["id"]
    command = msg["text"].split()[0].split("@")[0].lower()
    if command in ("/start", "/join"):
        subscribe(chat_id)
        send_message(chat_id, "Welcome to *BET SQUAD*.\n\nCommands:\n/today — scan today's available football markets\n/help — show commands\n\nThis bot provides research only. You place any bet manually and verify SportyBet's actual price.")
    elif command == "/help":
        send_message(chat_id, "/today — run today's scan\n/status — provider configuration status\n/help — commands")
    elif command == "/status":
        send_message(chat_id, f"API key configured: {'yes' if bool(API_KEY) else 'no'}\nComparison bookmaker: {BOOKMAKER_NAME} (ID {BOOKMAKER_ID})\nSports enabled: {', '.join(SUPPORTED_SPORTS)}\nMinimum estimated edge: {MIN_EDGE:.1%}")
    elif command == "/today":
        send_message(chat_id, "Scanning today's fixtures and real bookmaker markets. This may take a moment...")
        try:
            picks, count = scan_today()
            send_message(chat_id, fmt_report(picks, count))
        except Exception as exc:
            log.exception("Scan failed")
            send_message(chat_id, f"Scan failed safely: {str(exc)[:500]}\nNo fallback picks were generated.")
    elif command == "/admin_broadcast" and is_admin(chat_id):
        try:
            picks, count = scan_today()
            report = fmt_report(picks, count)
            for subscriber in subscriber_ids():
                try: send_message(subscriber, report)
                except Exception: log.exception("Broadcast failed for %s", subscriber)
        except Exception as exc:
            send_message(chat_id, f"Broadcast scan failed: {exc}")


def run_polling():
    if not BOT_TOKEN: raise RuntimeError("BET_BOT_KEY is missing.")
    db()
    offset = None
    log.info("BET SQUAD polling started")
    while True:
        try:
            params = {"timeout": 50}
            if offset is not None: params["offset"] = offset
            updates = tg_call("getUpdates", params)
            for update in updates:
                offset = update["update_id"] + 1
                conn = db()
                cur = conn.execute("INSERT OR IGNORE INTO telegram_updates(update_id) VALUES(?)",
                                   (update["update_id"],))
                fresh = cur.rowcount == 1
                conn.commit(); conn.close()
                if fresh: handle_update(update)
        except KeyboardInterrupt: break
        except Exception:
            log.exception("Polling error; retrying")
            time.sleep(5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", action="store_true", help="run a one-off scan and print report")
    parser.add_argument("--poll", action="store_true", help="run Telegram bot polling")
    args = parser.parse_args()
    if args.scan:
        picks, count = scan_today()
        print(fmt_report(picks, count))
    else:
        run_polling()


if __name__ == "__main__":
    main()
