"""
BET SQUAD — data-first Telegram betting research bot.
No fabricated fixtures, odds, probabilities or fallback selections.
SportyBet is the manual execution venue; comparison odds are not SportyBet prices.
"""
from __future__ import annotations

import os
import time
import math
import sqlite3
import logging
import argparse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

API_KEY = os.getenv("BET_API", "").strip()
BOT_TOKEN = os.getenv("BET_BOT_KEY", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
DB_PATH = os.getenv("BET_DB_PATH", "bet_squad.sqlite3")

FOOTBALL_BASE = "https://v3.football.api-sports.io"
BOOKMAKER_ID = int(os.getenv("BET_BOOKMAKER_ID", "7"))  # William Hill
BOOKMAKER_NAME = os.getenv("BET_BOOKMAKER_NAME", "William Hill")

TIMEOUT = 25
MIN_EDGE = float(os.getenv("BET_MIN_EDGE", "0.04"))
MAX_SELECTIONS = int(os.getenv("BET_MAX_SELECTIONS", "10"))
SUPPORTED_SPORTS = ["football"]  # Other sports stay disabled until API coverage is verified.
LAGOS_TZ = ZoneInfo("Africa/Lagos")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("bet-squad")


class ProviderError(RuntimeError):
    pass


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

    def recent_team_fixtures(
        self, team_id: int, before_date: str, limit: int = 10
    ) -> list[dict]:
        """Free-plan-compatible recent form retrieval using a date range."""
        try:
            match_date = datetime.strptime(before_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ProviderError(f"Invalid fixture date: {before_date}") from exc

        end_date = match_date - timedelta(days=1)
        start_date = end_date - timedelta(days=120)
        rows = self.get(
            "fixtures",
            team=team_id,
            **{
                "from": start_date.isoformat(),
                "to": end_date.isoformat(),
                "timezone": "Africa/Lagos",
            },
        )
        completed = [
            row for row in rows
            if row.get("fixture", {}).get("status", {}).get("short")
            in ("FT", "AET", "PEN")
        ]
        completed.sort(
            key=lambda row: row.get("fixture", {}).get("date", ""),
            reverse=True,
        )
        return completed[:limit]

    def fixture_odds(self, fixture_id: int) -> list[dict]:
        return self.get("odds", fixture=fixture_id, bookmaker=BOOKMAKER_ID)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS subscribers(
            chat_id TEXT PRIMARY KEY, joined_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS scans(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at TEXT NOT NULL, fixture_id INTEGER NOT NULL,
            sport TEXT NOT NULL, fixture TEXT NOT NULL, market TEXT NOT NULL,
            selection TEXT NOT NULL, odds REAL NOT NULL,
            model_probability REAL NOT NULL, implied_probability REAL NOT NULL,
            edge REAL NOT NULL, bookmaker TEXT NOT NULL, kickoff TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            UNIQUE(fixture_id, market, selection, scanned_at)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS telegram_updates(
            update_id INTEGER PRIMARY KEY
        )"""
    )
    conn.commit()
    return conn


def poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * (lam**k) / math.factorial(k)


def score_matrix(home_lambda: float, away_lambda: float, max_goals: int = 10):
    home_probs = [poisson_pmf(i, home_lambda) for i in range(max_goals + 1)]
    away_probs = [poisson_pmf(i, away_lambda) for i in range(max_goals + 1)]
    matrix = [[h * a for a in away_probs] for h in home_probs]
    total = sum(map(sum, matrix))
    return [[value / total for value in row] for row in matrix]


def market_probabilities(matrix):
    size = len(matrix)
    home = draw = away = over25 = under25 = btts_yes = btts_no = 0.0
    for hg in range(size):
        for ag in range(size):
            p = matrix[hg][ag]
            if hg > ag:
                home += p
            elif hg == ag:
                draw += p
            else:
                away += p
            if hg + ag >= 3:
                over25 += p
            else:
                under25 += p
            if hg > 0 and ag > 0:
                btts_yes += p
            else:
                btts_no += p

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
    scored = conceded = count = 0
    for row in rows:
        teams, goals = row.get("teams", {}), row.get("goals", {})
        is_home = teams.get("home", {}).get("id") == team_id
        own = goals.get("home" if is_home else "away")
        opponent = goals.get("away" if is_home else "home")
        if isinstance(own, int) and isinstance(opponent, int):
            scored += own
            conceded += opponent
            count += 1
    if count < 5:
        return None
    return {
        "scored": scored / count,
        "conceded": conceded / count,
        "matches": count,
    }


def estimate_lambdas(home_rates, away_rates):
    # Simple blended form estimate; not league-adjusted or calibrated.
    home_rate = (home_rates["scored"] + away_rates["conceded"]) / 2
    away_rate = (away_rates["scored"] + home_rates["conceded"]) / 2
    return max(0.15, min(4.0, home_rate)), max(0.15, min(4.0, away_rate))


def normalize_market_name(name: str) -> str:
    return " ".join(
        str(name).lower().replace("-", " ").replace("_", " ").replace("/", " ").split()
    )


def parse_selection(market: str, value: str):
    """
    Convert provider outcome labels to the model's canonical labels.
    Returns None for labels that cannot be mapped unambiguously.
    """
    label = normalize_market_name(value)
    market_key = normalize_market_name(market)

    if market_key in ("match winner", "1x2", "winner"):
        aliases = {
            "home": "home", "1": "home", "team 1": "home", "1st team": "home",
            "draw": "draw", "x": "draw",
            "away": "away", "2": "away", "team 2": "away", "2nd team": "away",
        }
    elif market_key in ("over under", "goals over under", "total goals"):
        aliases = {
            "over 2.5": "over 2.5", "over 2 5": "over 2.5",
            "under 2.5": "under 2.5", "under 2 5": "under 2.5",
        }
    elif market_key in ("both teams score", "btts"):
        aliases = {"yes": "yes", "no": "no"}
    else:
        return None
    return aliases.get(label)


def extract_odds(odds_payload):
    found = []
    for fixture_block in odds_payload:
        for bookmaker in fixture_block.get("bookmakers", []):
            if bookmaker.get("id") != BOOKMAKER_ID:
                continue
            for bet in bookmaker.get("bets", []):
                market_name = normalize_market_name(bet.get("name", ""))
                if market_name in ("match winner", "1x2", "winner"):
                    market = "match winner"
                    required = {"home", "draw", "away"}
                elif market_name in ("over under", "goals over under", "total goals"):
                    market = "over/under"
                    required = {"over 2.5", "under 2.5"}
                elif market_name in ("both teams score", "btts"):
                    market = "both teams score"
                    required = {"yes", "no"}
                else:
                    continue

                # Keep only the exact line/model outcomes supported by this engine.
                prices = {}
                for item in bet.get("values", []):
                    selection = parse_selection(market, item.get("value", ""))
                    try:
                        price = float(item.get("odd"))
                    except (TypeError, ValueError):
                        continue
                    if selection and price > 1.0:
                        prices[selection] = price

                # Never calculate a market margin from an incomplete outcome set.
                if required.issubset(prices):
                    for selection, price in prices.items():
                        found.append((market, selection, price))
                else:
                    log.info(
                        "Rejecting incomplete %s market; found %s, need %s",
                        market, sorted(prices), sorted(required)
                    )
    return found


def analyse_fixture(api: ApiFootball, fixture: dict):
    info = fixture.get("fixture", {})
    teams, league = fixture.get("teams", {}), fixture.get("league", {})
    home, away = teams.get("home", {}), teams.get("away", {})
    if not all((info.get("id"), home.get("id"), away.get("id"))):
        return []

    kickoff = info.get("date", "")
    before_date = kickoff[:10]
    home_rates = team_goal_rates(api, home["id"], before_date)
    away_rates = team_goal_rates(api, away["id"], before_date)
    if not home_rates or not away_rates:
        log.info("Skip %s: insufficient recent form", info.get("id"))
        return []

    hl, al = estimate_lambdas(home_rates, away_rates)
    probabilities = market_probabilities(score_matrix(hl, al))
    try:
        odds_data = extract_odds(api.fixture_odds(info["id"]))
    except ProviderError as exc:
        log.warning("Odds unavailable for fixture %s: %s", info["id"], exc)
        return []

    grouped = {}
    for market, selection, price in odds_data:
        grouped.setdefault(market, {})[selection] = price

    candidates = []
    required_outcomes = {
        "match winner": {"home", "draw", "away"},
        "over/under": {"over 2.5", "under 2.5"},
        "both teams score": {"yes", "no"},
    }
    for market, outcomes in grouped.items():
        # Defensive validation remains here even though extract_odds validates too.
        if not required_outcomes[market].issubset(outcomes):
            continue
        reciprocal_sum = sum(1.0 / price for price in outcomes.values())
        if reciprocal_sum <= 0:
            continue
        for selection, price in outcomes.items():
            probability = probabilities.get(market, {}).get(selection)
            if probability is None:
                continue
            implied = (1.0 / price) / reciprocal_sum
            edge = probability - implied
            if edge >= MIN_EDGE:
                candidates.append({
                    "fixture_id": info["id"], "sport": "Football",
                    "fixture": f'{home.get("name")} vs {away.get("name")}',
                    "league": league.get("name", "Unknown league"),
                    "market": market, "selection": selection, "odds": price,
                    "probability": probability, "implied": implied, "edge": edge,
                    "bookmaker": BOOKMAKER_NAME, "kickoff": kickoff,
                    "home_matches": home_rates["matches"],
                    "away_matches": away_rates["matches"],
                    "lambda_home": hl, "lambda_away": al,
                })
    return candidates


def scan_today():
    api = ApiFootball(API_KEY)
    today = datetime.now(LAGOS_TZ).date().isoformat()
    fixtures = api.fixtures(today)
    picks = []
    for fixture in fixtures:
        if fixture.get("fixture", {}).get("status", {}).get("short") != "NS":
            continue
        try:
            picks.extend(analyse_fixture(api, fixture))
        except ProviderError as exc:
            log.warning("Skipping fixture due to provider error: %s", exc)

    picks.sort(key=lambda item: item["edge"], reverse=True)
    selected, used = [], set()
    for pick in picks:
        if pick["fixture_id"] in used:
            continue
        selected.append(pick)
        used.add(pick["fixture_id"])
        if len(selected) >= MAX_SELECTIONS:
            break

    conn = db()
    scanned_at = datetime.now(timezone.utc).isoformat()
    for pick in selected:
        conn.execute(
            """INSERT OR IGNORE INTO scans
            (scanned_at, fixture_id, sport, fixture, market, selection,
             odds, model_probability, implied_probability, edge,
             bookmaker, kickoff)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scanned_at, pick["fixture_id"], pick["sport"], pick["fixture"],
                pick["market"], pick["selection"], pick["odds"],
                pick["probability"], pick["implied"], pick["edge"],
                pick["bookmaker"], pick["kickoff"],
            ),
        )
    conn.commit()
    conn.close()
    return selected, len(fixtures)


def fmt_report(picks, fixture_count):
    if not picks:
        return (
            "🛡️ *BET SQUAD — NO BET SLATE*\\n\\n"
            f"Scanned {fixture_count} fixtures. No selection passed "
            "the current data and edge filters.\\n"
            "No odds or predictions were invented."
        )
    lines = [
        "🧠 *BET SQUAD — DATA SCREEN*",
        f"Fixtures checked: {fixture_count}",
        f"Qualified candidates: {len(picks)}", "",
    ]
    for i, pick in enumerate(picks, 1):
        lines.append(
            f"*{i}. {pick['fixture']}*\\n"
            f"🏆 {pick['league']} | {pick['kickoff']}\\n"
            f"Market: {pick['market']} — *{pick['selection']}*\\n"
            f"Comparison odds ({pick['bookmaker']}): *{pick['odds']:.2f}*\\n"
            f"Model probability: {pick['probability']:.1%} | "
            f"Implied: {pick['implied']:.1%}\\n"
            f"Estimated edge: {pick['edge']:.1%}\\n"
            f"Form sample: {pick['home_matches']} / {pick['away_matches']} games\\n"
        )
    lines.append(
        "⚠️ Comparison odds are NOT SportyBet odds. Verify the exact "
        "fixture, market, selection and current price on SportyBet. "
        "Probabilities are estimates, not guarantees."
    )
    return "\\n".join(lines)


def tg_call(method, payload=None):
    if not BOT_TOKEN:
        raise RuntimeError("BET_BOT_KEY is missing.")
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json=payload or {}, timeout=TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(str(data))
    return data["result"]


def send_message(chat_id, message):
    for start in range(0, len(message), 3900):
        tg_call("sendMessage", {
            "chat_id": chat_id, "text": message[start:start + 3900],
            "parse_mode": "Markdown", "disable_web_page_preview": True,
        })


def subscribe(chat_id):
    conn = db()
    conn.execute(
        "INSERT OR IGNORE INTO subscribers(chat_id, joined_at) VALUES (?, ?)",
        (str(chat_id), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def subscriber_ids():
    conn = db()
    ids = [row["chat_id"] for row in conn.execute("SELECT chat_id FROM subscribers")]
    conn.close()
    return ids


def is_admin(chat_id):
    return bool(ADMIN_ID) and str(chat_id) == str(ADMIN_ID)


def handle_update(update):
    message = update.get("message") or update.get("edited_message")
    if not message or not message.get("text"):
        return
    chat_id = message["chat"]["id"]
    command = message["text"].split()[0].split("@")[0].lower()

    if command in ("/start", "/join"):
        subscribe(chat_id)
        send_message(
            chat_id,
            "Welcome to *BET SQUAD*.\\n\\n"
            "Commands:\\n/today — scan today's available football markets\\n"
            "/help — show commands\\n\\n"
            "Research only. Verify SportyBet's actual price before betting.",
        )
    elif command == "/help":
        send_message(chat_id, "/today — run today's scan\\n/status — provider status\\n/help — commands")
    elif command == "/status":
        send_message(
            chat_id,
            f"API key configured: {'yes' if bool(API_KEY) else 'no'}\\n"
            f"Comparison bookmaker: {BOOKMAKER_NAME} (ID {BOOKMAKER_ID})\\n"
            f"Sports enabled: {', '.join(SUPPORTED_SPORTS)}\\n"
            f"Minimum estimated edge: {MIN_EDGE:.1%}",
        )
    elif command == "/today":
        send_message(chat_id, "Scanning today's fixtures and real bookmaker markets...")
        try:
            picks, count = scan_today()
            send_message(chat_id, fmt_report(picks, count))
        except Exception as exc:
            log.exception("Scan failed")
            send_message(chat_id, f"Scan failed safely: {str(exc)[:500]}\\nNo fallback picks were generated.")
    elif command == "/admin_broadcast" and is_admin(chat_id):
        try:
            picks, count = scan_today()
            report = fmt_report(picks, count)
            for subscriber in subscriber_ids():
                try:
                    send_message(subscriber, report)
                except Exception:
                    log.exception("Broadcast failed for %s", subscriber)
        except Exception as exc:
            send_message(chat_id, f"Broadcast scan failed: {exc}")


def run_polling():
    if not BOT_TOKEN:
        raise RuntimeError("BET_BOT_KEY is missing.")
    db()
    offset = None
    log.info("BET SQUAD polling started")
    while True:
        try:
            params = {"timeout": 50}
            if offset is not None:
                params["offset"] = offset
            updates = tg_call("getUpdates", params)
            for update in updates:
                offset = update["update_id"] + 1
                conn = db()
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO telegram_updates(update_id) VALUES (?)",
                    (update["update_id"],),
                )
                is_new = cursor.rowcount == 1
                conn.commit()
                conn.close()
                if is_new:
                    handle_update(update)
        except KeyboardInterrupt:
            break
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
