# Crypto coin monitor

A strict GitHub Actions monitor for BTC, BNB, BONK, ZEC, SOL, Sleepless AI (`AI`) and ORDI.

It runs at **01:13, 07:13, 13:13 and 19:13 UTC**—equivalent to **09:13, 15:13, 21:13 and 03:13 Beijing/Tokyo time**. This fixed UTC schedule is intentionally 13 minutes after the hour and does not observe daylight saving time.

## Required repository configuration

Add these under **Settings → Secrets and variables → Actions**:

| Secret | Required | Purpose |
|---|---|---|
| `COINGLASS_API_KEY` | Optional, preferred | CoinGlass primary circulating market cap and aggregated open interest; without it the public CoinGlass asset page is parsed |
| `COINGECKO_API_KEY` | Optional | CoinGecko market-cap verification; its public asset page is used when the unauthenticated endpoint is unavailable |
| `COINALYZE_API_KEY` | Optional | Coinalyze OI verification; its public instrument page is used when no key exists |
| `CRYPTO_ALERT_WEBHOOK_URL` | Optional | Slack-compatible incoming webhook; only newly confirmed anomalies are posted |

No key is stored in the repository. Public-page extraction accepts only labelled machine-readable values; bot blocks, stale/cached pages, or unparseable pages produce `N/A`. Without a primary CoinGlass value, `last_complete` is deliberately not advanced.

## State and safeguards

- `state/monitor-state.json` stores `latest`, `last_complete`, `previous_complete`, raw payloads, source errors and confirmed anomaly state.
- A complete snapshot requires all seven Binance spot prices plus CoinGlass circulating market cap and aggregated OI, with `AI` explicitly identified as Sleepless AI.
- The workflow uses a queueing concurrency lock, 18-minute timeout, per-request retries, and three rebasing push attempts to protect state from overlapping runs.
- Daily comparison is valid only when consecutive complete snapshots are 23:45–24:15 apart; otherwise it reports `N/A`.

## Run once

Open **Actions → Crypto monitor → Run workflow** and select `main`. Inspect the job summary and commit created by the run. Configure the secrets first if a complete baseline is required.
