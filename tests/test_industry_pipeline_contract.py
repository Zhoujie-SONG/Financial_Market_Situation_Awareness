import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from pipelines.industry import IndustryPipeline
from pipelines.single_stock import SingleStockPipeline, normalize_score


@pytest.mark.parametrize("score,expected", [(0, 0.0), (1, 0.01), (4, 0.04), (60, 0.6), (100, 1.0),
                                           ("78", 0.78), (None, 0.5), (float("nan"), 0.5)])
def test_industry_summary_uses_explicit_hundred_point_score(score, expected):
    assert normalize_score(score) == expected


def test_summary_signal_and_score_use_same_validated_value():
    result = SimpleNamespace(sentiment_score="4", dashboard={}, analysis_summary="test", market_snapshot={})
    summary = SingleStockPipeline._compress_to_summary(result)
    assert summary["score"] == 0.04
    assert summary["signal"] == "strong_sell"


def test_industry_request_delay_is_reached_and_failed_stock_is_retained():
    pipeline = IndustryPipeline(config=SimpleNamespace(), concurrency=1, request_delay=0.1)
    worker = Mock()
    worker.analyze_single.side_effect = [{"code": "000001", "score": .5}, RuntimeError("fixture failure")]
    pipeline._single_stock_pipeline = worker
    stocks = [{"code": "000001", "name": "one"}, {"code": "000002", "name": "two"}]
    with patch("pipelines.industry.asyncio.sleep", new_callable=AsyncMock) as delay:
        results = asyncio.run(pipeline._micro_analysis_async(stocks))
    assert delay.await_count == 2
    assert len(results) == 2
    assert results[1]["status"] == "failed"


def test_industry_invalid_concurrency_fails_before_running():
    with pytest.raises(ValueError, match="concurrency"):
        IndustryPipeline(config=SimpleNamespace(), concurrency=0)
