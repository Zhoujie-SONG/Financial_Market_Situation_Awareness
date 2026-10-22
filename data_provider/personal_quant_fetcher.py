"""Optional local canonical daily-bar provider for the existing analysis workflows."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from .base import BaseFetcher, DataFetchError, STANDARD_COLUMNS
from src.services.personal_quant import PersonalQuantOptions, PersonalQuantReader, canonical_etf_symbol


class PersonalQuantFetcher(BaseFetcher):
    name = "PersonalQuantFetcher"
    priority = -1

    def __init__(self, options: PersonalQuantOptions, reader=None):
        self.options = options
        self.reader = reader or PersonalQuantReader(options)

    @staticmethod
    def supports(stock_code: str) -> bool:
        return canonical_etf_symbol(stock_code) is not None

    def get_daily_data(self, stock_code, start_date=None, end_date=None, days=30):
        as_of = datetime.now(timezone.utc)
        if not self.supports(stock_code):
            raise DataFetchError("PersonalQuantFetcher only serves mainland ETFs")
        end = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else as_of.astimezone(
            ZoneInfo("Asia/Shanghai")
        ).date()
        start = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else end - timedelta(days=days * 2)
        result = self.reader.query(stock_code, start, end, as_of=as_of, mode="system_replay")
        if not result["bars"]:
            raise DataFetchError("No eligible finalized canonical daily bars; ingest in personal-quant first")
        frame = pd.DataFrame([
            {
                "date": row["trade_date"],
                **{key: float(row[key]) for key in ("open", "high", "low", "close")},
                "volume": row["volume"], "amount": float(row["turnover"]),
            }
            for row in result["bars"]
        ])
        frame["pct_chg"] = frame["close"].pct_change(fill_method=None) * 100
        frame = self._calculate_indicators(self._clean_data(frame))
        frame.attrs["personal_quant"] = result["provenance"]
        frame.attrs["resolved_etf_metadata"] = result["metadata"]
        return frame

    def _fetch_raw_data(self, stock_code, start_date, end_date):
        return self.get_daily_data(stock_code, start_date, end_date)

    def _normalize_data(self, df, stock_code):
        return df.loc[:, STANDARD_COLUMNS].copy()
