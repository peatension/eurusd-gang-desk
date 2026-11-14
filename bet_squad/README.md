# BET SQUAD

A data-first Telegram research bot. It does not place bets. SportyBet is the manual execution venue; the configured comparison bookmaker is William Hill (API-Sports bookmaker ID 7, based on the user's successful test response).

## Current implemented scope

- Real API-Football fixtures for the current date.
- Recent completed team fixtures (up to 10; requires at least 5 for each team).
- A transparent Poisson goal model for 1X2, over/under 2.5 and BTTS where the API returns matching William Hill markets.
- Odds parsed only from the configured bookmaker. No hard-coded prices or fallback picks.
- Minimum estimated edge filter and one candidate per fixture.
- SQLite storage for subscribers, scan records and Telegram update de-duplication.
- Telegram commands `/start`, `/join`, `/today`, `/status`, `/help`, plus admin `/admin_broadcast`.
- GitHub Actions daily scan at 09:00 Nigeria time and manual run.

## Important limitations — do not mistake these for completed features

- Football only is enabled. Other sports are deliberately not enabled until their API host, fixture schema, odds endpoint, market catalogue and subscription coverage are verified against the user's API plan.
- Model is an initial transparent Poisson form model, not yet calibrated or backtested. Its edge is a research signal, not proof of positive expected value.
- Only bookmaker ID 7 is configured. The user previously confirmed William Hill data on a test fixture. Coverage is fixture/league/market dependent.
- The GitHub Actions job is a daily scan, not a continuous live tracker. Live tracking and settlement audit are not implemented in this release.
- SQLite in GitHub Actions is ephemeral. Persistent subscriber and historical records require a persistent host/database before production use.
- No slip compiler: do not multiply independent-looking picks into a multi-leg accumulator until correlation and historical calibration are handled.

## Required GitHub secrets

- `BET_API`: API-Sports key
- `BET_BOT_KEY`: Telegram bot token (needed for the always-on Telegram polling deployment)
- `ADMIN_ID`: Telegram numeric admin chat ID

Never put credentials in source code or chat.

## Run a one-off scan

```bash
pip install requests
export BET_API="your-key"
python bet_squad_bot.py --scan
```

## Run Telegram polling

```bash
export BET_API="your-key"
export BET_BOT_KEY="your-token"
export ADMIN_ID="your-chat-id"
python bet_squad_bot.py --poll
```

Polling needs an always-on host. GitHub Actions scheduled jobs are not a suitable always-on bot process.

## Test

```bash
python -m unittest discover -s tests -v
```
