"""Market data: daily-bar sources, the look-ahead guard and the data quality gate."""

from aitrader.data.adjusted import (
    CorporateAction,
    CorporateActionError,
    CorporateActionKind,
    PointInTimeBars,
    detect_corporate_actions,
)
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
    "CorporateAction",
    "CorporateActionError",
    "CorporateActionKind",
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
    "PointInTimeBars",
    "SymbolReport",
    "detect_corporate_actions",
    "fetch_universe",
    "session_of",
    "write_daily_csv",
]
