# -*- coding: utf-8 -*-
"""
===================================
单股分析管线 (SingleStockPipeline)
===================================

封装"单只/多只股票分析"的完整流程：
  抓取数据 → 技术分析 → 搜索新闻 → LLM 分析 → 报告生成

设计要点：
- 可独立运行（模式 A 的入口）
- 可被 IndustryPipeline 内部循环调用
- analyze_single() 输出精简版摘要供行业聚合使用，内置 Token 截断
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.config import Config

from pipelines.base import BasePipeline

logger = logging.getLogger(__name__)

# Token 截断阈值（供行业聚合时使用的精简摘要）
_SUMMARY_MAX_CHARS = 500        # 一句话总结最多 500 字符
_METRIC_MAX_ITEMS = 5           # 关键指标最多 5 项
_METRIC_MAX_VALUE_CHARS = 100   # 每项指标值最多 100 字符
_RISK_MAX_ITEMS = 5             # 风险标签最多 5 条


def _truncate(text: str, max_chars: int) -> str:
    """截断文本，超出部分加省略号。"""
    if not text or len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


def _signal_from_score(score: int) -> str:
    """从情绪评分推导信号标签。"""
    if score >= 70:
        return "strong_buy"
    elif score >= 60:
        return "buy"
    elif score >= 40:
        return "hold"
    elif score >= 30:
        return "sell"
    else:
        return "strong_sell"


class SingleStockPipeline(BasePipeline):
    """
    单股分析管线。

    对现有 StockAnalysisPipeline 的轻量封装，提供：
    1. run()            — 批量分析多只股票（保持原功能）
    2. analyze_single() — 分析单只股票，返回精简摘要（供行业聚合）
    """

    def __init__(self, config: Optional[Config] = None):
        super().__init__(config)
        self._pipeline = None  # 延迟初始化

    # ──────────────────────────────────────────────
    # 内部: 延迟加载 StockAnalysisPipeline
    # ──────────────────────────────────────────────

    def _get_pipeline(self):
        """延迟加载现有的 StockAnalysisPipeline，避免 import 循环。"""
        if self._pipeline is None:
            from src.core.pipeline import StockAnalysisPipeline

            self._pipeline = StockAnalysisPipeline(config=self.config)
        return self._pipeline

    # ──────────────────────────────────────────────
    # 批量分析（模式 A 主入口）
    # ──────────────────────────────────────────────

    def run(
        self,
        stock_codes: List[str],
        dry_run: bool = False,
        send_notification: bool = True,
        **kwargs,
    ) -> List[Any]:
        """
        批量分析多只股票。

        Args:
            stock_codes: 股票代码列表。
            dry_run:     仅获取数据，不进行 AI 分析。
            send_notification: 是否发送推送通知。
            **kwargs:    透传给 StockAnalysisPipeline.run() 的额外参数。

        Returns:
            AnalysisResult 列表。
        """
        pipeline = self._get_pipeline()
        logger.info(
            "[SingleStockPipeline] 开始分析 %d 只股票 (dry_run=%s)",
            len(stock_codes),
            dry_run,
        )
        return pipeline.run(
            stock_codes=stock_codes,
            dry_run=dry_run,
            send_notification=send_notification,
            **kwargs,
        )

    # ──────────────────────────────────────────────
    # 单股分析（供 IndustryPipeline 内部调用）
    # ──────────────────────────────────────────────

    def analyze_single(self, stock_code: str) -> Dict[str, Any]:
        """
        分析单只股票，返回**精简版摘要**。

        内部调用 StockAnalysisPipeline.run([stock_code]) 获取完整 AnalysisResult，
        然后压缩为适合行业聚合的精简结构，并自动截断超出限制的文本。

        Args:
            stock_code: 股票代码。

        Returns:
            精简版分析摘要:
            {
                "code": "300308",
                "name": "中际旭创",
                "signal": "buy",             # strong_buy/buy/hold/sell/strong_sell
                "score": 0.78,               # 归一化到 0.0~1.0
                "confidence": "高",           # 高/中/低
                "summary": "一句话结论…",     # ≤500 字符
                "decision_type": "buy",       # buy/hold/sell
                "key_metrics": {              # 关键数据指标
                    "trend": "MA5>MA10>MA20",
                    "price_position": "",
                    "volume": "",
                },
                "risk_flags": [...],          # 风险标签列表
                "sniper_points": {...},       # 买卖点位
            }

            失败时返回:
            {
                "code": "...",
                "error": "错误信息",
                "status": "failed",
            }
        """
        logger.info("[analyze_single] 分析股票: %s", stock_code)

        try:
            # 1. 调用现有管线（单只股票，不发送通知）
            pipeline = self._get_pipeline()
            results = pipeline.run(
                stock_codes=[stock_code],
                send_notification=False,
                dry_run=False,
            )

            # 2. 提取分析结果
            if not results:
                return {
                    "code": stock_code,
                    "error": "分析未返回结果",
                    "status": "failed",
                }

            result = results[0]
            if not result.success:
                return {
                    "code": stock_code,
                    "error": result.error_message or "分析失败",
                    "status": "failed",
                }

            # 3. 压缩为精简摘要
            return self._compress_to_summary(result)

        except Exception as exc:
            logger.warning(
                "[analyze_single] %s 分析异常: %s", stock_code, exc
            )
            return {
                "code": stock_code,
                "error": str(exc),
                "status": "failed",
            }

    # ──────────────────────────────────────────────
    # 摘要压缩与 Token 截断
    # ──────────────────────────────────────────────

    @staticmethod
    def _compress_to_summary(result: Any) -> Dict[str, Any]:
        """
        将 AnalysisResult 压缩为精简摘要。

        Token 截断策略:
        - summary: ≤500 字符
        - key_metrics: ≤5 项，每项值 ≤100 字符
        - risk_flags: ≤5 条
        """
        dash = result.dashboard if isinstance(result.dashboard, dict) else {}

        # ── 核心字段 ──
        raw_score = getattr(result, "sentiment_score", 50)
        score = normalize_score(raw_score)
        signal = _signal_from_score(raw_score)
        decision_type = (
            getattr(result, "decision_type", "")
            or infer_decision_from_score(raw_score)
        )

        # ── 一句话结论 ──
        summary_text = getattr(result, "analysis_summary", "") or ""
        summary_text = _truncate(summary_text, _SUMMARY_MAX_CHARS)

        # ── 关键指标 ──
        key_metrics = _extract_key_metrics(result, dash)

        # ── 风险标签 ──
        risk_flags = _extract_risk_flags(dash)

        # ── 买卖点位 ──
        sniper_points = _extract_sniper_points(dash)

        return {
            "code": getattr(result, "code", ""),
            "name": getattr(result, "name", ""),
            "signal": signal,
            "score": score,
            "confidence": getattr(result, "confidence_level", "中"),
            "summary": summary_text,
            "decision_type": decision_type,
            "key_metrics": key_metrics,
            "risk_flags": risk_flags,
            "sniper_points": sniper_points,
        }


# ──────────────────────────────────────────────
# 辅助函数（模块级，方便 IndustryPipeline 复用）
# ──────────────────────────────────────────────


def normalize_score(raw_score: Any) -> float:
    """
    将各种格式的评分归一化到 0.0 ~ 1.0 浮点数。

    输入可能是:
    - int (0-100, 源码默认 50)
    - float (0.0-100.0, 部分数据源)
    - str
    """
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        return 0.5

    # 如果 > 5 则认为是 0-100 制
    if abs(score) > 5:
        return max(0.0, min(1.0, score / 100.0))
    # 如果 ≤ 1 则已归一化
    if abs(score) <= 1:
        return max(0.0, min(1.0, score))
    # 否则 clip
    return max(0.0, min(1.0, score / 10.0))


def infer_decision_from_score(score: int) -> str:
    """从综合评分推断决策类型。"""
    if score >= 70:
        return "buy"
    elif score >= 60:
        return "buy"
    elif score >= 40:
        return "hold"
    else:
        return "sell"


def _extract_key_metrics(result: Any, dash: dict) -> Dict[str, str]:
    """
    从 AnalysisResult 中提取关键技术指标。

    提取来源: dashboard.data_perspective + 直接属性。
    输出键为中文名（原因：下游 CIO Agent 使用中文 Prompt）。
    """
    metrics: Dict[str, str] = {}

    # 趋势分析
    trend = getattr(result, "trend_prediction", "") or ""
    if trend:
        metrics["趋势"] = _truncate(str(trend), _METRIC_MAX_VALUE_CHARS)

    # 均线分析
    ma = getattr(result, "ma_analysis", "") or ""
    if ma:
        metrics["均线"] = _truncate(str(ma), _METRIC_MAX_VALUE_CHARS)

    # 量能分析
    vol = getattr(result, "volume_analysis", "") or ""
    if vol:
        metrics["量能"] = _truncate(str(vol), _METRIC_MAX_VALUE_CHARS)

    # data_perspective 中的价格位置
    dp = dash.get("data_perspective") or {}
    if isinstance(dp, dict):
        pp = dp.get("price_position") or {}
        if isinstance(pp, dict):
            for k in ("ma5", "ma10", "ma20", "bias_ma5"):
                v = pp.get(k)
                if v is not None:
                    label = {
                        "ma5": "MA5",
                        "ma10": "MA10",
                        "ma20": "MA20",
                        "bias_ma5": "乖离率",
                    }.get(k, k)
                    # bias_ma5 可能是百分比，需要格式化
                    if isinstance(v, (int, float)):
                        if k == "bias_ma5":
                            metrics[label] = f"{v:+.1f}%"
                        else:
                            metrics[label] = f"{v:.2f}"
                    else:
                        metrics[label] = _truncate(str(v), _METRIC_MAX_VALUE_CHARS)

        # 筹码结构
        cs = dp.get("chip_structure") or {}
        if isinstance(cs, dict):
            profit = cs.get("profit_ratio")
            conc = cs.get("concentration_90")
            if profit is not None:
                try:
                    metrics["获利比例"] = f"{float(profit)*100:.0f}%"
                except (TypeError, ValueError):
                    pass
            if conc is not None:
                try:
                    metrics["筹码集中度"] = f"{float(conc)*100:.1f}%"
                except (TypeError, ValueError):
                    pass

    # 截断到最大项数
    if len(metrics) > _METRIC_MAX_ITEMS:
        keys = list(metrics.keys())[:_METRIC_MAX_ITEMS]
        metrics = {k: metrics[k] for k in keys}

    return metrics


def _extract_risk_flags(dash: dict) -> List[str]:
    """
    从 dashboard.intelligence.risk_alerts 提取风险标签。
    保证返回 ≤ RISK_MAX_ITEMS 条。
    """
    intel = dash.get("intelligence") or {}
    if not isinstance(intel, dict):
        return []

    risk_alerts = intel.get("risk_alerts") or []
    if not isinstance(risk_alerts, list):
        return []

    # 去重、去空、截断
    cleaned: List[str] = []
    seen = set()
    for item in risk_alerts:
        text = str(item).strip() if item else ""
        if text and text not in seen:
            cleaned.append(text)
            seen.add(text)
        if len(cleaned) >= _RISK_MAX_ITEMS:
            break

    return cleaned


def _extract_sniper_points(dash: dict) -> Dict[str, Any]:
    """
    从 dashboard.battle_plan.sniper_points 提取买卖点位。
    """
    battle = dash.get("battle_plan") or {}
    if not isinstance(battle, dict):
        return {}

    sp = battle.get("sniper_points") or {}
    if not isinstance(sp, dict):
        return {}

    # 只保留关键值，去掉冗长的文本解释
    compact: Dict[str, Any] = {}
    for key in ("ideal_buy", "secondary_buy", "stop_loss", "target"):
        val = sp.get(key)
        if val is not None:
            # 如果是纯数字就直接用，否则截断文本
            if isinstance(val, (int, float)):
                compact[key] = val
            else:
                compact[key] = _truncate(str(val), 50)

    return compact
