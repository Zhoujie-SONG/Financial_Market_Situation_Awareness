"""Optional local canonical daily-bar provider for the existing analysis workflows."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from .base import BaseFetcher, DataFetchError, STANDARD_COLUMNS
from src.services.personal_quant import (
    PersonalQuantOptions, PersonalQuantReader, canonical_etf_symbol, count_missing_sessions,
)


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
        current_view = end_date is None
        end = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else as_of.astimezone(
            ZoneInfo("Asia/Shanghai")
        ).date()
        start = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else end - timedelta(days=days * 2)
        result = self.reader.query(stock_code, start, end, as_of=as_of, mode="system_replay")
        if not result["bars"]:
            raise DataFetchError("No eligible finalized canonical daily bars; ingest in personal-quant first")
        provenance = dict(result["provenance"])
        if current_view:
            self._check_freshness(provenance, as_of)

        bars = result["bars"]
        breaks = provenance.get("price_discontinuities") or []
        if breaks:
            # Unadjusted prices before a share conversion are on another scale; keep
            # only the bars after the latest break so moving averages stay comparable.
            window_start = breaks[-1]["trade_date"]
            bars = [row for row in bars if row["trade_date"] >= window_start]
            provenance["analysis_window_start"] = window_start

        frame = pd.DataFrame([
            {
                "date": row["trade_date"],
                **{key: float(row[key]) for key in ("open", "high", "low", "close")},
                "volume": row["volume"], "amount": float(row["turnover"]),
            }
            for row in bars
        ])
        frame["pct_chg"] = frame["close"].pct_change(fill_method=None) * 100
        frame = self._calculate_indicators(self._clean_data(frame))
        frame.attrs["personal_quant"] = provenance
        frame.attrs["resolved_etf_metadata"] = result["metadata"]
        return frame

    def _check_freshness(self, provenance: dict, as_of: datetime) -> None:
        """Refuse a current-view analysis when local bars lag the latest closed session."""
        from src.core.trading_calendar import get_effective_trading_date, is_market_open

        last = date.fromisoformat(str(provenance["last_trade_date"])[:10])
        expected = get_effective_trading_date("cn", current_time=as_of)
        missing = count_missing_sessions(last, expected, lambda day: is_market_open("cn", day))
        provenance["expected_trade_date"] = expected.isoformat()
        provenance["missing_sessions"] = missing
        if missing > self.options.max_stale_sessions:
            raise DataFetchError(
                f"Canonical bars end at {last.isoformat()}, {missing} trading session(s) before "
                f"{expected.isoformat()}; run personal-quant ingestion first "
                f"(PERSONAL_QUANT_MAX_STALE_SESSIONS={self.options.max_stale_sessions})"
            )

    def _fetch_raw_data(self, stock_code, start_date, end_date):
        return self.get_daily_data(stock_code, start_date, end_date)

    def _normalize_data(self, df, stock_code):
        return df.loc[:, STANDARD_COLUMNS].copy()
