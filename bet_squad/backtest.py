
"""Historical, leakage-aware evaluation for BET SQUAD's football model.

Run from the repository root:
    python bet_squad/backtest.py --days 90

This evaluates forecast quality only. It deliberately does not calculate betting
ROI because the provider does not retain a sufficiently long historical odds archive.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Works both as `python bet_squad/backtest.py` and under unittest discovery.
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from bet_squad_bot import (  # noqa: E402
    ApiFootball,
    ProviderError,
    estimate_lambdas,
    market_probabilities,
    score_matrix,
)

COMPLETED = {"FT", "AET", "PEN"}
LOOKBACK_DAYS = 120
FORM_MATCHES = 10
MIN_FORM_MATCHES = 5
EPSILON = 1e-12

# API-Football IDs for Europe's top five domestic leagues.
TOP_FIVE_LEAGUES = {
    39: "Premier League",
    140: "La Liga",
    78: "Bundesliga",
    135: "Serie A",
    61: "Ligue 1",
}


def parse_kickoff(row: dict[str, Any]) -> datetime | None:
    raw = row.get("fixture", {}).get("date")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def valid_completed_fixture(row: dict[str, Any]) -> bool:
    fixture = row.get("fixture", {})
    teams = row.get("teams", {})
    goals = row.get("goals", {})
    home = teams.get("home", {})
    away = teams.get("away", {})
    return (
        fixture.get("status", {}).get("short") in COMPLETED
        and bool(fixture.get("id"))
        and bool(home.get("id"))
        and bool(away.get("id"))
        and isinstance(goals.get("home"), int)
        and isinstance(goals.get("away"), int)
    )


def form_rates(history: deque, cutoff: datetime) -> dict[str, float | int] | None:
    """Use at most the 10 latest results in the 120 days before cutoff."""
    cutoff_floor = cutoff - timedelta(days=LOOKBACK_DAYS)
    eligible = [
        item for item in history
        if cutoff_floor <= item[0] < cutoff and item[0].date() < cutoff.date()
    ]
    eligible = eligible[-FORM_MATCHES:]
    if len(eligible) < MIN_FORM_MATCHES:
        return None
    return {
        "scored": sum(item[1] for item in eligible) / len(eligible),
        "conceded": sum(item[2] for item in eligible) / len(eligible),
        "matches": len(eligible),
    }


def _score(probabilities: dict[str, float], outcome: str) -> tuple[float, float]:
    """Return multiclass Brier sum and clipped log loss for one observation."""
    brier = sum(
        (p - (1.0 if key == outcome else 0.0)) ** 2
        for key, p in probabilities.items()
    )
    log_loss = -math.log(max(EPSILON, min(1.0, probabilities[outcome])))
    return brier, log_loss


def evaluate_rows(
    rows: list[dict[str, Any]],
    target_start: datetime,
    target_end: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Replay matches in timestamp batches so same-kickoff results cannot leak."""
    usable = []
    seen_ids = set()

    for row in rows:
        if not valid_completed_fixture(row):
            continue
        kickoff = parse_kickoff(row)
        fixture_id = row.get("fixture", {}).get("id")
        if kickoff is None or fixture_id in seen_ids:
            continue
        seen_ids.add(fixture_id)
        usable.append((kickoff, row))

    usable.sort(key=lambda pair: pair[0])

    history: dict[int, deque] = defaultdict(deque)
    predictions: list[dict[str, Any]] = []
    index = 0

    while index < len(usable):
        kickoff = usable[index][0]
        batch = []

        while index < len(usable) and usable[index][0] == kickoff:
            batch.append(usable[index][1])
            index += 1

        # Predict every match at this kickoff before adding same-time results.
        if target_start <= kickoff <= target_end:
            for row in batch:
                fixture = row["fixture"]
                teams = row["teams"]
                goals = row["goals"]
                home = teams["home"]
                away = teams["away"]

                home_rates = form_rates(history[home["id"]], kickoff)
                away_rates = form_rates(history[away["id"]], kickoff)

                if not home_rates or not away_rates:
                    continue

                home_lambda, away_lambda = estimate_lambdas(
                    home_rates, away_rates
                )
                markets = market_probabilities(
                    score_matrix(home_lambda, away_lambda)
                )

                actual_home = goals["home"]
                actual_away = goals["away"]

                actuals = {
                    "1x2": (
                        "home" if actual_home > actual_away
                        else "away" if actual_home < actual_away
                        else "draw"
                    ),
                    "over_under_2_5": (
                        "over 2.5"
                        if actual_home + actual_away >= 3
                        else "under 2.5"
                    ),
                    "btts": (
                        "yes"
                        if actual_home > 0 and actual_away > 0
                        else "no"
                    ),
                }

                probs = {
                    "1x2": markets["1x2"],
                    "over_under_2_5": markets["over/under"],
                    "btts": markets["btts"],
                }

                record = {
                    "fixture_id": fixture.get("id"),
                    "kickoff_utc": kickoff.isoformat(),
                    "league": row.get("league", {}).get("name", "Unknown"),
                    "home_team": home.get("name", str(home.get("id"))),
                    "away_team": away.get("name", str(away.get("id"))),
                    "actual_score": f"{actual_home}-{actual_away}",
                    "home_form_matches": home_rates["matches"],
                    "away_form_matches": away_rates["matches"],
                    "lambda_home": round(home_lambda, 6),
                    "lambda_away": round(away_lambda, 6),
                }

                for market_name, market_probs in probs.items():
                    actual = actuals[market_name]
                    brier, loss = _score(market_probs, actual)

                    record[f"{market_name}_actual"] = actual
                    record[f"{market_name}_prediction"] = max(
                        market_probs, key=market_probs.get
                    )
                    record[f"{market_name}_brier"] = brier
                    record[f"{market_name}_log_loss"] = loss

                    for outcome, probability in market_probs.items():
                        record[
                            f"{market_name}_p_{outcome.replace(' ', '_')}"
                        ] = probability

                predictions.append(record)

        # Only now are results from this kickoff added to team histories.
        for row in batch:
            home_id = row["teams"]["home"]["id"]
            away_id = row["teams"]["away"]["id"]
            home_goals = row["goals"]["home"]
            away_goals = row["goals"]["away"]

            history[home_id].append(
                (kickoff, home_goals, away_goals)
            )
            history[away_id].append(
                (kickoff, away_goals, home_goals)
            )

    summary: dict[str, Any] = {
        "target_start_utc": target_start.isoformat(),
        "target_end_utc": target_end.isoformat(),
        "historical_fixtures_received": len(rows),
        "completed_unique_fixtures": len(usable),
        "fixtures_with_sufficient_prior_form": len(predictions),
        "metrics": {},
        "note": (
            "Forecast-quality evaluation only; "
            "no historical odds or ROI inferred."
        ),
    }

    metric_keys = {
        "1x2": (
            "1x2_brier",
            "1x2_log_loss",
            "1x2_prediction",
            "1x2_actual",
        ),
        "over_under_2_5": (
            "over_under_2_5_brier",
            "over_under_2_5_log_loss",
            "over_under_2_5_prediction",
            "over_under_2_5_actual",
        ),
        "btts": (
            "btts_brier",
            "btts_log_loss",
            "btts_prediction",
            "btts_actual",
        ),
    }

    for market, (brier_key, loss_key, pred_key, actual_key) in metric_keys.items():
        if not predictions:
            summary["metrics"][market] = {"n": 0}
            continue

        n = len(predictions)
        summary["metrics"][market] = {
            "n": n,
            "accuracy": sum(
                p[pred_key] == p[actual_key] for p in predictions
            ) / n,
            "mean_brier_score": sum(
                p[brier_key] for p in predictions
            ) / n,
            "mean_log_loss": sum(
                p[loss_key] for p in predictions
            ) / n,
        }

    return predictions, summary


def season_years_for_range(start: date, end: date) -> list[int]:
    """Return European season start years intersecting the requested dates."""
    years = []

    for year in range(start.year - 1, end.year + 1):
        season_start = date(year, 7, 1)
        season_end = date(year + 1, 6, 30)

        if season_start <= end and season_end >= start:
            years.append(year)

    return years


def fetch_history(
    api: ApiFootball,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    """Fetch top-five league fixtures with required league/season filters."""
    rows: list[dict[str, Any]] = []
    seen_ids = set()

    seasons = season_years_for_range(start, end)

    for league_id in TOP_FIVE_LEAGUES:
        for season in seasons:
            batch = api.get(
                "fixtures",
                league=league_id,
                season=season,
                **{
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                    "timezone": "UTC",
                },
            )

            for row in batch:
                fixture_id = row.get("fixture", {}).get("id")

                if fixture_id and fixture_id not in seen_ids:
                    seen_ids.add(fixture_id)
                    rows.append(row)

    return rows


def write_outputs(
    predictions: list[dict[str, Any]],
    summary: dict[str, Any],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "backtest_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    csv_path = output_dir / "backtest_predictions.csv"

    if predictions:
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(predictions[0]),
            )
            writer.writeheader()
            writer.writerows(predictions)
    else:
        csv_path.write_text(
            "No fixtures had sufficient prior form.\n",
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run BET SQUAD historical forecast backtest"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="number of target days (default: 90)",
    )
    parser.add_argument(
        "--end",
        help="target end date YYYY-MM-DD (default: today UTC)",
    )
    parser.add_argument(
        "--output",
        default="bet_squad/backtest_output",
        help="output directory",
    )

    args = parser.parse_args()

    if not 1 <= args.days <= 365:
        parser.error("--days must be between 1 and 365")

    try:
        end_day = (
            date.fromisoformat(args.end)
            if args.end
            else datetime.now(timezone.utc).date()
        )
    except ValueError:
        parser.error("--end must use YYYY-MM-DD")

    target_start_day = end_day - timedelta(days=args.days - 1)
    fetch_start = target_start_day - timedelta(days=LOOKBACK_DAYS)

    target_start = datetime.combine(
        target_start_day,
        datetime.min.time(),
        timezone.utc,
    )
    target_end = datetime.combine(
        end_day,
        datetime.max.time(),
        timezone.utc,
    )

    try:
        api = ApiFootball(os.getenv("BET_API", "").strip())

        rows = fetch_history(api, fetch_start, end_day)

        predictions, summary = evaluate_rows(
            rows,
            target_start,
            target_end,
        )

        summary["api_fetch_from"] = fetch_start.isoformat()
        summary["api_fetch_to"] = end_day.isoformat()

        write_outputs(
            predictions,
            summary,
            Path(args.output),
        )

    except ProviderError as exc:
        print(
            f"Backtest stopped safely: {exc}",
            file=sys.stderr,
        )
        return 2

    print(json.dumps(summary, indent=2))
    print(
        f"\nDetailed predictions: "
        f"{args.output}/backtest_predictions.csv"
    )
    print(
        f"Summary: {args.output}/backtest_summary.json"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
