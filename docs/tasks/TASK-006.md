# TASK-006: Market data ingestion from Alpaca (read-only), universe U0, data checks

Implementer: Claude (owner instruction 2026-10-23). Owner signed up for the Alpaca account on
2026-10-24 (decision Q2: data only). Design sources: documents 02 (§5 data source), 05 (§4
data quality, DATA-3), 11 (§2 universe), 12 (§2 adjusted vs. raw prices, §5 data quality).

Status: **code done** (2026-10-24); 100% coverage, all checks green. **The real download is
pending:** the operator runs it on the owner's PC once the owner has set the key there as
user environment variables (`CLAUDE.md`, "Credentials").

Facts used (Alpaca documentation, read 2026-10-24): `GET https://data.alpaca.markets/v2/stocks/bars`;
headers `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY`; `timeframe=1Day`; `adjustment` = `raw`,
`split`, `dividend`, `spin-off` or `all`; `feed` default `sip`; `limit` up to 10,000 bars per
page with `next_page_token`; results sorted by symbol, then time. Free (Basic) plan: history
since 2016, SIP data except the latest 15 minutes, 200 historical calls per minute.

## Scope

1. **Universe U0** (`config/universe.yaml`): 23 liquid US ETFs, fixed before any backtest.
2. **Client** (`aitrader.data.alpaca`): read-only; only the host `data.alpaca.markets` is
   accepted; explicit timeouts; GET retries with backoff on 429 and 5xx only; pagination;
   prices parsed as exact decimals from the JSON text; Alpaca's daily timestamps (midnight New
   York) mapped to NYSE session dates; typed errors (`DataAuthError` for 401,
   `DataPermissionError` for 403 = DATA-3, `DataSourceError` otherwise). Credentials come only
   from `AITRADER_DATA_API_KEY` / `AITRADER_DATA_API_SECRET` and are never logged.
3. **Store:** two CSV series per symbol in the format `DailyBarSource` reads: `raw/` (for fills)
   and `adjusted/` (`adjustment=all`, for signals). Files are written atomically.
4. **Checks and report** (`fetch_report.json`): rows and first/last session per symbol, missing
   sessions against the exchange calendar (DQ-2), daily moves above 25% (DQ-3), and corporate
   actions found by comparing raw with split-adjusted prices (DQ-5).
5. **CLI:** `aitrader data fetch` (universe, period, output directory).

## Out of scope

Live quotes and the PAPER data feed (Phase 3), a second source for DQ-4, using the adjusted
series for signals and booking dividends in backtests (TASK-007).

## Deliberate deviation

The design names Parquet as the store. At this size (23 ETFs × about 2,250 sessions) CSV files
are small, readable, byte-stable for the data snapshot hash and need no extra dependency. The
store can move to Parquet later behind the same `DailyBarSource` interface.

## Acceptance criteria

- Tests with recorded responses cover pagination, retries, the host allow-list, error mapping,
  decimal parsing and session dates; no network in tests.
- The real download runs on the operator PC with the key in user environment variables.
- Real market data is never committed to git (`/data/` is ignored).
- ruff, mypy (strict), lint-imports and pytest green; coverage ≥ 90%.
