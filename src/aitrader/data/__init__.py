"""Market data: daily-bar sources, the look-ahead guard and the data quality gate."""

from aitrader.data.alpaca import (
    AlpacaDataClient,
    DailyBarRow,
    DataAuthError,
    DataPermissionError,
    DataSourceError,
)
from aitrader.data.bars import DailyBarSource, DataError, LookAheadError, MarketDataView, session_of
from aitrader.data.ingest import FetchReport, SymbolReport, fetch_universe, write_daily_csv
from aitrader.data.quality import DataQualityGate

__all__ = [
    "AlpacaDataClient",
    "DailyBarRow",
    "DailyBarSource",
    "DataAuthError",
    "DataError",
    "DataPermissionError",
    "DataQualityGate",
    "DataSourceError",
    "FetchReport",
    "LookAheadError",
    "MarketDataView",
    "SymbolReport",
    "fetch_universe",
    "session_of",
    "write_daily_csv",
]
