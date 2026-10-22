"""Offline integration tests using immutable observations in temporary repositories."""
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from src.services.personal_quant import (
    PersonalQuantError, PersonalQuantOptions, PersonalQuantReader,
    attach_result_provenance, bind_daily_snapshot, canonical_etf_symbol,
    frame_analysis_context, get_daily_snapshot, main, reset_daily_snapshot,
)


UTC = timezone.utc


@pytest.fixture
def canonical_store(tmp_path):
    pytest.importorskip("etf_quant")
    from etf_quant.data.repositories.market_repository import ParquetMarketRepository
    from etf_quant.domain.enums import HistoricalDataSemantics
    from etf_quant.domain.models.market_bar import MarketBar

    root = tmp_path / "canonical"
    repository = ParquetMarketRepository(root)
    economic_time = datetime(2019, 1, 2, 7, 15, tzinfo=UTC)
    first_ingest = datetime(2026, 1, 1, tzinfo=UTC)
    bar = MarketBar(
        symbol="510300.SH", trade_date=date(2019, 1, 2),
        open=Decimal("4"), high=Decimal("4.1"), low=Decimal("3.9"), close=Decimal("4"),
        volume=100, turnover=Decimal("400"), data_time=economic_time.replace(minute=0),
        available_time=economic_time, ingest_time=first_ingest, source="longbridge",
        availability_policy_id="daily_bar_eod_v1_15m",
        historical_data_semantics=HistoricalDataSemantics.HISTORICAL_LATEST,
    )
    repository.append_bars([bar, replace(bar, close=Decimal("4.01"), ingest_time=first_ingest.replace(day=2))])
    return root, repository, bar


def _query(root, **overrides):
    parameters = dict(
        symbol="510300.SH", start_date=date(2019, 1, 1), end_date=date(2019, 1, 3),
        as_of=datetime(2019, 1, 3, tzinfo=UTC), mode="economic",
        research_data_cutoff=datetime(2026, 1, 1, 12, tzinfo=UTC),
    )
    parameters.update(overrides)
    return PersonalQuantReader(PersonalQuantOptions(root)).query(**parameters)


def test_economic_history_uses_cutoff_before_revision_selection(canonical_store):
    root, _repository, _bar = canonical_store
    first = _query(root)
    second = _query(root, research_data_cutoff=datetime(2026, 1, 3, tzinfo=UTC))
    assert Decimal(first["bars"][0]["close"]) == Decimal("4")
    assert Decimal(second["bars"][0]["close"]) == Decimal("4.01")
    assert first["provenance"]["dataset_snapshot_id"] != second["provenance"]["dataset_snapshot_id"]
    assert _query(root) == first


def test_system_replay_cannot_see_later_downloads(canonical_store):
    root, _repository, _bar = canonical_store
    assert _query(root, mode="system_replay")["bars"] == []
    replay = _query(root, as_of=datetime(2026, 1, 3, tzinfo=UTC), mode="system_replay")
    assert Decimal(replay["bars"][0]["close"]) == Decimal("4")
    assert replay["provenance"]["adjust_type"] == "none"


def test_reader_never_modifies_canonical_files_or_creates_metadata_db(canonical_store):
    root, _repository, _bar = canonical_store
    before = {path.relative_to(root): sha256(path.read_bytes()).hexdigest() for path in root.rglob("*") if path.is_file()}
    assert _query(root)["metadata"]["status"] == "UNAVAILABLE"
    after = {path.relative_to(root): sha256(path.read_bytes()).hexdigest() for path in root.rglob("*") if path.is_file()}
    assert before == after
    assert not (root / "instrument_metadata.sqlite3").exists()


@pytest.mark.parametrize("field", ["as_of", "research_data_cutoff"])
def test_naive_datetimes_rejected(canonical_store, field):
    root, _repository, _bar = canonical_store
    with pytest.raises(PersonalQuantError, match="timezone-aware"):
        _query(root, **{field: datetime(2026, 1, 1)})


def test_economic_query_requires_dataset_cutoff(canonical_store):
    root, _repository, _bar = canonical_store
    with pytest.raises(PersonalQuantError, match="explicit research_data_cutoff"):
        _query(root, research_data_cutoff=None)


def test_ambiguous_latest_observations_fail_fast(canonical_store):
    root, repository, bar = canonical_store
    repository.append_bars([replace(bar, close=Decimal("4.02"))])
    with pytest.raises(PersonalQuantError, match="Ambiguous canonical revisions"):
        _query(root)


def test_canonical_export_cli_writes_json_snapshot(canonical_store, tmp_path):
    import json
    root, _repository, _bar = canonical_store
    output = tmp_path / "export.json"
    assert main([
        "--canonical-dir", str(root), "--symbol", "510300.SH", "--start-date", "2019-01-01",
        "--end-date", "2019-01-03", "--as-of", "2019-01-03T00:00:00+00:00",
        "--mode", "economic", "--research-data-cutoff", "2026-01-01T12:00:00+00:00",
        "--output", str(output),
    ]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["provenance"]["row_count"] == 1


def test_export_cli_cannot_overwrite_canonical_source(canonical_store):
    root, _repository, _bar = canonical_store
    with pytest.raises(SystemExit) as failure:
        main([
            "--canonical-dir", str(root), "--symbol", "510300.SH", "--start-date", "2019-01-01",
            "--end-date", "2019-01-03", "--as-of", "2026-01-03T00:00:00+00:00",
            "--mode", "system_replay", "--output", str(root / "overwrite.json"),
        ])
    assert failure.value.code == 1
    assert not (root / "overwrite.json").exists()


@pytest.mark.parametrize("code,expected", [
    ("510300", "510300.SH"), ("SH510300", "510300.SH"), ("510300.SH", "510300.SH"),
    ("159915.sz", "159915.SZ"), ("AAPL", None), ("HK00700", None), ("600519", None), ("160105", None),
])
def test_etf_symbol_boundary(code, expected):
    assert canonical_etf_symbol(code) == expected


def test_wrong_explicit_exchange_cannot_be_silently_normalized():
    with pytest.raises(PersonalQuantError, match="exchange"):
        canonical_etf_symbol("510300.SZ")


def test_manager_strict_mode_stops_before_legacy_fallback(tmp_path):
    from data_provider.base import DataFetchError, DataFetcherManager
    from data_provider.personal_quant_fetcher import PersonalQuantFetcher
    canonical = PersonalQuantFetcher(PersonalQuantOptions(tmp_path), reader=Mock())
    canonical.reader.query.side_effect = PersonalQuantError("missing finalized bars")
    legacy = Mock(name="legacy")
    legacy.name, legacy.priority = "LegacyFetcher", 0
    manager = DataFetcherManager([canonical, legacy])
    with pytest.raises(DataFetchError, match="missing finalized bars"):
        manager.get_daily_data("510300.SH")
    legacy.get_daily_data.assert_not_called()


def test_manager_only_uses_legacy_after_explicit_opt_in(tmp_path):
    from data_provider.base import DataFetcherManager
    from data_provider.personal_quant_fetcher import PersonalQuantFetcher
    canonical = PersonalQuantFetcher(PersonalQuantOptions(tmp_path, allow_fallback=True), reader=Mock())
    canonical.reader.query.side_effect = PersonalQuantError("missing finalized bars")
    legacy = Mock()
    legacy.name, legacy.priority = "LegacyFetcher", 0
    legacy.get_daily_data.return_value = pd.DataFrame({"close": [4.0]})
    manager = DataFetcherManager([canonical, legacy])
    frame, source = manager.get_daily_data("510300.SH")
    assert source == "LegacyFetcher"
    assert frame.iloc[0]["close"] == 4.0


def test_fetcher_converts_units_and_preserves_provenance(canonical_store):
    from data_provider.personal_quant_fetcher import PersonalQuantFetcher
    root, _repository, _bar = canonical_store
    frame = PersonalQuantFetcher(PersonalQuantOptions(root)).get_daily_data(
        "510300", start_date="2019-01-01", end_date="2019-01-03",
    )
    assert frame.iloc[0]["volume"] == 100
    assert frame.iloc[0]["amount"] == 400
    assert frame.iloc[0]["close"] == 4.01
    assert frame.attrs["personal_quant"]["mode"] == "system_replay"
    assert pd.isna(frame.iloc[0]["pct_chg"])


def test_pipeline_bypasses_mutable_cache_and_binds_canonical_snapshot(canonical_store):
    from src.core.pipeline import StockAnalysisPipeline
    from data_provider.personal_quant_fetcher import PersonalQuantFetcher
    root, _repository, _bar = canonical_store
    frame = PersonalQuantFetcher(PersonalQuantOptions(root)).get_daily_data(
        "510300", start_date="2019-01-01", end_date="2019-01-03",
    )
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.fetcher_manager = Mock()
    pipeline.fetcher_manager.uses_personal_quant.return_value = True
    pipeline.fetcher_manager.get_daily_data.return_value = frame, "PersonalQuantFetcher"
    pipeline.db = Mock()
    pipeline.db.has_today_data.return_value = True
    token = bind_daily_snapshot("510300", None)
    try:
        with patch.object(pipeline, "_resolve_resume_target_date", return_value=date(2019, 1, 2)):
            assert pipeline.fetch_and_save_stock_data("510300") == (True, None)
        pipeline.db.has_today_data.assert_not_called()
        pipeline.db.save_daily_data.assert_not_called()
        context = frame_analysis_context("510300", get_daily_snapshot("510300"))
        assert context["today"]["close"] == 4.01
        result = SimpleNamespace(market_snapshot=None, data_sources="analysis")
        attach_result_provenance(result, "510300")
        attach_result_provenance(result, "510300")
        assert result.data_sources.count("PersonalQuantFetcher") == 1
        assert result.market_snapshot["data_provenance"]["dataset_snapshot_id"].startswith("sha256:")
    finally:
        reset_daily_snapshot(token)


def test_agent_history_uses_bound_snapshot_instead_of_db():
    from src.services.history_loader import load_history_df
    frame = pd.DataFrame({"date": pd.to_datetime(["2026-01-02"]), "close": [4.0]})
    token = bind_daily_snapshot("510300", frame)
    try:
        with patch("src.storage.get_db") as database:
            result, source = load_history_df("510300", target_date=date(2026, 1, 2))
            database.assert_not_called()
        assert source == "PersonalQuantFetcher"
        assert result.iloc[0]["close"] == 4.0
    finally:
        reset_daily_snapshot(token)
    assert get_daily_snapshot("510300") is None


def test_failed_canonical_input_never_reuses_legacy_cache():
    from src.core.pipeline import StockAnalysisPipeline
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.fetcher_manager = Mock()
    pipeline.fetcher_manager.uses_personal_quant.return_value = True
    pipeline.fetch_and_save_stock_data = Mock(return_value=(False, "canonical unavailable"))
    pipeline.analyze_stock = Mock()
    pipeline._resolve_resume_target_date = Mock(return_value=date(2026, 1, 2))
    assert pipeline.process_single_stock("510300") is None
    pipeline.analyze_stock.assert_not_called()
    assert get_daily_snapshot("510300") is None


def test_metadata_is_read_only_and_retains_field_status(canonical_store, tmp_path):
    import sqlite3
    import yaml
    from etf_quant.config.metadata_resolution import ETF_METADATA_FIELDS
    from etf_quant.data.repositories.metadata_repository import MetadataRepository

    root, _repository, _bar = canonical_store
    MetadataRepository(root)  # Create an empty fixture DB, not application code.
    path = root / "instrument_metadata.sqlite3"
    before = sha256(path.read_bytes()).hexdigest()
    policy = tmp_path / "policy.yaml"
    policy.write_text(yaml.safe_dump({
        "schema_version": 1, "policy_id": "fixture_policy", "known_sources": ["fixture"],
        "same_source_temporal_ordering": ["effective_from", "snapshot_at", "available_time", "ingest_time"],
        "same_source_identity_tiebreaker": "provider_payload_hash",
        "freshness_clock": "SNAPSHOT_AT_OR_AVAILABLE_TIME",
        "fields": {name: {"source_precedence": ["fixture"], "max_age_seconds": None,
                          "conflict_policy": "REQUIRE_AGREEMENT"} for name in ETF_METADATA_FIELDS},
    }), encoding="utf-8")
    reader = PersonalQuantReader(PersonalQuantOptions(root, policy))
    result = reader.query("510300", date(2019, 1, 1), date(2019, 1, 3),
                          as_of=datetime(2026, 1, 3, tzinfo=UTC), mode="system_replay")
    assert result["metadata"]["resolved"]["fields"]["fund_name"]["status"] == "UNKNOWN"
    assert sha256(path.read_bytes()).hexdigest() == before
    # sqlite read-only URI also rejects accidental write statements.
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE forbidden (id INTEGER)")
