"""Read-only bridge to personal-quant's immutable market and metadata observations.

The application uses current SYSTEM_REPLAY data. The module CLI exposes explicit
historical queries without invoking news, real-time quotes, LLMs, or notifications.
etf_quant is optional and is imported only when this bridge is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class PersonalQuantError(ValueError):
    """Configuration, compatibility, or canonical data failed validation."""


def canonical_etf_symbol(code: str) -> Optional[str]:
    """Accept mainland ETF codes; preserve and validate explicit exchange suffixes."""
    value = str(code or "").strip().upper()
    match = re.fullmatch(r"(?:(SH|SZ))?(\d{6})(?:\.(SH|SZ|SS))?", value)
    if not match:
        return None
    prefix, digits, suffix = match.groups()
    if digits.startswith(("51", "52", "56", "58")):
        expected = "SH"
    elif digits.startswith("15"):
        expected = "SZ"
    else:
        return None
    suffix = "SH" if suffix == "SS" else suffix
    if any(exchange and exchange != expected for exchange in (prefix, suffix)):
        raise PersonalQuantError("ETF exchange prefix/suffix disagrees with its code")
    return f"{digits}.{expected}"


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PersonalQuantError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=_json_default, ensure_ascii=False, allow_nan=False))


@dataclass(frozen=True)
class PersonalQuantOptions:
    canonical_dir: Path
    metadata_policy: Optional[Path] = None
    allow_fallback: bool = False

    @classmethod
    def from_config(cls, config: Any) -> Optional["PersonalQuantOptions"]:
        root = getattr(config, "personal_quant_canonical_dir", None)
        if not root:
            return None
        policy = getattr(config, "personal_quant_metadata_policy", None)
        return cls(
            canonical_dir=Path(root).expanduser().resolve(),
            metadata_policy=Path(policy).expanduser().resolve() if policy else None,
            allow_fallback=getattr(config, "personal_quant_allow_fallback", False) is True,
        )


class PersonalQuantReader:
    """Use etf_quant's repositories/resolver without opening a writable source DB."""

    def __init__(self, options: PersonalQuantOptions):
        self.options = options

    def query(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        *,
        as_of: datetime,
        mode: str,
        research_data_cutoff: Optional[datetime] = None,
    ) -> dict:
        canonical_symbol = canonical_etf_symbol(symbol)
        if canonical_symbol is None:
            raise PersonalQuantError("personal-quant bridge currently supports mainland ETFs only")
        as_of = _aware(as_of, "as_of")
        if mode not in {"economic", "system_replay"}:
            raise PersonalQuantError("mode must be economic or system_replay")
        if mode == "economic" and research_data_cutoff is None:
            raise PersonalQuantError("economic queries require an explicit research_data_cutoff")
        cutoff = _aware(research_data_cutoff or as_of, "research_data_cutoff")
        if start_date > end_date:
            raise PersonalQuantError("start_date cannot follow end_date")
        if not (self.options.canonical_dir / "market_bars").is_dir():
            raise PersonalQuantError("Canonical market_bars directory is missing; ingest data in personal-quant first")
        try:
            from etf_quant.data.repositories.market_repository import ParquetMarketRepository
            from etf_quant.domain.enums import CanonicalMarketSource, PITQueryMode
        except ImportError as exc:
            raise PersonalQuantError("Install requirements-personal-quant.txt to enable the canonical bridge") from exc

        query_mode = PITQueryMode(mode)
        repository = ParquetMarketRepository(self.options.canonical_dir)
        # First use the upstream API to enforce schema compatibility and source/PIT eligibility.
        days = repository.get_bars(
            canonical_symbol, start_date, end_date,
            source=CanonicalMarketSource.LONGBRIDGE, as_of=as_of, mode=query_mode,
        )
        selected = []
        for day in days:
            revisions = repository.get_bar_revisions(canonical_symbol, day.trade_date, "longbridge")
            eligible = [
                item for item in revisions
                if item.bar.available_time <= as_of
                and item.bar.ingest_time <= cutoff
                and (mode == "economic" or item.bar.ingest_time <= as_of)
            ]
            if not eligible:
                continue
            latest_time = max(item.bar.ingest_time for item in eligible)
            latest = [item for item in eligible if item.bar.ingest_time == latest_time]
            if len({item.value_hash for item in latest}) > 1:
                raise PersonalQuantError("Ambiguous canonical revisions share the latest ingestion time")
            # Hash is representation only, after data value agreement.
            selected.append(max(latest, key=lambda item: item.observation_id))

        rows = [
            {**_json_safe(asdict(item.bar)), "observation_id": item.observation_id, "value_hash": item.value_hash}
            for item in selected
        ]
        metadata = self._metadata(canonical_symbol, as_of, query_mode, cutoff)
        provenance = {
            "source": "longbridge",
            "adapter": "PersonalQuantFetcher",
            "symbol": canonical_symbol,
            "as_of": as_of.isoformat(),
            "mode": mode,
            "research_data_cutoff": cutoff.isoformat(),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "adjust_type": "none",
            "historical_data_semantics": sorted({row["historical_data_semantics"] for row in rows}),
            "availability_policy_ids": sorted({row["availability_policy_id"] for row in rows}),
            "row_count": len(rows),
            "first_trade_date": rows[0]["trade_date"] if rows else None,
            "last_trade_date": rows[-1]["trade_date"] if rows else None,
            "metadata_status": metadata["status"],
        }
        identity = json.dumps(
            {"provenance": provenance, "bars": rows, "metadata": metadata},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        provenance["dataset_snapshot_id"] = "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return {"provenance": provenance, "bars": rows, "metadata": metadata}

    def _metadata(self, symbol: str, as_of: datetime, mode: Any, cutoff: datetime) -> dict:
        db_path = self.options.canonical_dir / "instrument_metadata.sqlite3"
        if not db_path.is_file():
            return {"status": "UNAVAILABLE", "reason": "metadata_database_missing"}
        if self.options.metadata_policy is None:
            return {"status": "UNAVAILABLE", "reason": "metadata_policy_not_configured"}
        if not self.options.metadata_policy.is_file():
            raise PersonalQuantError("Configured metadata resolution policy does not exist")
        from etf_quant.config.metadata_resolution import MetadataResolutionPolicy
        from etf_quant.data.repositories.metadata_repository import MetadataRepository
        from etf_quant.services.metadata_resolution import MetadataResolver

        class ReadOnlyMetadataRepository(MetadataRepository):
            def __init__(self, path: Path):
                self.path = path

            @contextmanager
            def _connect(self):
                connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
                try:
                    yield connection
                finally:
                    connection.close()

        policy = MetadataResolutionPolicy.from_yaml(self.options.metadata_policy)
        resolved = MetadataResolver(ReadOnlyMetadataRepository(db_path), policy).resolve(
            symbol, as_of=as_of, mode=mode, research_data_cutoff=cutoff,
        )
        return {"status": "FIELD_RESOLVED", "resolved": _json_safe(resolved.to_dict())}


# A pipeline worker binds one immutable input snapshot for downstream tools. It
# never writes canonical data into the application's mutable stock_daily cache.
_daily_snapshot: ContextVar[Optional[tuple[str, Any]]] = ContextVar("personal_quant_daily_snapshot", default=None)


def bind_daily_snapshot(code: str, frame: Any):
    return _daily_snapshot.set((canonical_etf_symbol(code) or code, frame))


def reset_daily_snapshot(token) -> None:
    _daily_snapshot.reset(token)


def get_daily_snapshot(code: str):
    current = _daily_snapshot.get()
    if current is None or current[0] != canonical_etf_symbol(code):
        return None
    return current[1]


def frame_analysis_context(code: str, frame: Any) -> dict:
    """Build the existing report context from the bound canonical input only."""
    import pandas as pd

    def row_payload(row):
        payload = {key: None if pd.isna(value) else value for key, value in row.items()}
        payload["date"] = pd.Timestamp(payload["date"]).date().isoformat()
        payload["code"] = code
        payload["data_source"] = "PersonalQuantFetcher"
        return payload

    latest = row_payload(frame.iloc[-1])
    previous = row_payload(frame.iloc[-2]) if len(frame) > 1 else {}
    context = {
        "code": code, "date": latest["date"], "today": latest, "yesterday": previous,
        "data_provenance": frame.attrs["personal_quant"],
        "resolved_etf_metadata": frame.attrs.get("resolved_etf_metadata", {}),
    }
    if previous.get("volume"):
        context["volume_change_ratio"] = round(latest["volume"] / previous["volume"], 2)
    if previous.get("close"):
        context["price_change_ratio"] = round((latest["close"] / previous["close"] - 1) * 100, 2)
    return context


def attach_result_provenance(result: Any, code: str) -> None:
    frame = get_daily_snapshot(code)
    if result is None or frame is None:
        return
    provenance = frame.attrs["personal_quant"]
    result.market_snapshot = {**(result.market_snapshot or {}), "data_provenance": provenance}
    sources = list(filter(None, (result.data_sources or "").split(",")))
    for source in ("PersonalQuantFetcher", provenance["dataset_snapshot_id"]):
        if source not in sources:
            sources.append(source)
    result.data_sources = ",".join(sources)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Export a read-only personal-quant PIT dataset snapshot")
    parser.add_argument("--canonical-dir", type=Path, default=os.getenv("PERSONAL_QUANT_CANONICAL_DIR"))
    parser.add_argument("--metadata-policy", type=Path, default=os.getenv("PERSONAL_QUANT_METADATA_POLICY"))
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--as-of", required=True, type=datetime.fromisoformat)
    parser.add_argument("--mode", required=True, choices=["economic", "system_replay"])
    parser.add_argument("--research-data-cutoff", type=datetime.fromisoformat)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.canonical_dir is None:
        parser.error("--canonical-dir or PERSONAL_QUANT_CANONICAL_DIR is required")
    try:
        result = PersonalQuantReader(PersonalQuantOptions(args.canonical_dir, args.metadata_policy)).query(
            args.symbol, args.start_date, args.end_date, as_of=args.as_of,
            mode=args.mode, research_data_cutoff=args.research_data_cutoff,
        )
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output:
            if args.output.resolve().is_relative_to(args.canonical_dir.resolve()):
                raise PersonalQuantError("Export output must be outside the read-only canonical directory")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(text + "\n")
        else:
            print(text)
        return 0
    except (ValueError, ImportError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"personal-quant query failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
