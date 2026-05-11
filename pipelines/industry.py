# -*- coding: utf-8 -*-
"""
===================================
行业深度分析管线 (IndustryPipeline)
===================================

Top-Down 行业深度分析流程:

  Step 1 (宏观): 搜索行业主题新闻
                → LLM 生成行业景气度与宏观逻辑分析

  Step 2 (微观): 读取行业成分股列表
                → 并发调用 SingleStockPipeline.analyze_single()

  Step 3 (聚合): 行业逻辑 + 各企业微观表现合并
                → CIO Agent 生成行业全景研报（Phase 4 实现）

设计要点:
- 并发处理成分股，使用 asyncio.Semaphore + 请求间隔
- Token 截断：单股分析输出精简版摘要（已在 SingleStockPipeline 中实现）
- 容错机制：单股/宏观失败不阻塞整体流程
- 惰性初始化 SearchService 和 LLM 客户端
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from src.config import Config

from pipelines.base import BasePipeline

logger = logging.getLogger(__name__)

# 默认并发数: 行业模式下避免触发 API Rate Limit
DEFAULT_INDUSTRY_CONCURRENCY = 3
# 请求间隔（秒）: 成分股分析之间的冷却时间
DEFAULT_INDUSTRY_REQUEST_DELAY = 2.0
# 宏观分析 Prompt 最大 Token
MACRO_MAX_TOKENS = 2048
# 宏观分析温度（稍高以获取更多洞见）
MACRO_TEMPERATURE = 0.5

# ──────────────────────────────────────────────────────
# 行业宏观分析 System Prompt
# ──────────────────────────────────────────────────────
INDUSTRY_MACRO_SYSTEM_PROMPT = """你是一位资深行业研究员。你的任务是对给定行业进行宏观层面的深度分析。

请严格按以下 JSON 格式输出，不要输出其他内容：
{
  "sentiment_summary": "<行业景气度一句话总结>",
  "outlook": "bullish" | "bearish" | "neutral",
  "confidence": 0.0~1.0,
  "core_logic": "<驱动行业的核心逻辑，3-5个要点，用换行符分隔>",
  "risk_factors": ["<风险1>", "<风险2>", ...],
  "positive_catalysts": ["<积极催化因素1>", "<积极催化因素2>", ...],
  "key_events": ["<重大事件1>", "<重大事件2>", ...],
  "policy_impact": "<政策面影响分析>",
  "cycle_position": "<行业周期位置判断（导入期/成长期/成熟期/衰退期）>",
  "competitive_landscape": "<竞争格局简析>"
}

注意：
- 结合搜索到的新闻信息，不要凭空编造
- 区分短期情绪波动和长期产业趋势
- 关注技术突破、产能扩张、下游需求变化等关键驱动力
"""


class IndustryPipeline(BasePipeline):
    """
    行业深度分析管线。

    用法:
        pipeline = IndustryPipeline(config=config)
        results = pipeline.run(industry_id="cpo")      # 分析单个行业
        results = pipeline.run()                        # 分析所有行业
    """

    def __init__(
        self,
        config: Optional[Config] = None,
        concurrency: int = DEFAULT_INDUSTRY_CONCURRENCY,
        request_delay: float = DEFAULT_INDUSTRY_REQUEST_DELAY,
    ):
        super().__init__(config)
        self.concurrency = concurrency
        self.request_delay = request_delay

        # 惰性初始化
        self._single_stock_pipeline = None
        self._search_service = None
        self._llm_analyzer = None

    # ──────────────────────────────────────────────
    # 惰性初始化
    # ──────────────────────────────────────────────

    def _get_single_stock_pipeline(self):
        if self._single_stock_pipeline is None:
            from pipelines.single_stock import SingleStockPipeline

            self._single_stock_pipeline = SingleStockPipeline(config=self.config)
        return self._single_stock_pipeline

    def _get_search_service(self):
        """惰性初始化搜索服务。"""
        if self._search_service is not None:
            return self._search_service

        try:
            from src.search_service import SearchService

            self._search_service = SearchService(
                bocha_keys=self.config.bocha_api_keys,
                tavily_keys=self.config.tavily_api_keys,
                anspire_keys=self.config.anspire_api_keys,
                brave_keys=self.config.brave_api_keys,
                serpapi_keys=self.config.serpapi_keys,
                minimax_keys=self.config.minimax_api_keys,
                searxng_base_urls=self.config.searxng_base_urls,
                searxng_public_instances_enabled=self.config.searxng_public_instances_enabled,
                news_max_age_days=getattr(self.config, "news_max_age_days", 7),
                news_strategy_profile=getattr(self.config, "news_strategy_profile", "short"),
            )
            logger.info("[搜索] 服务初始化完成")
        except Exception as exc:
            logger.warning("[搜索] 服务初始化失败，将以无搜索模式运行: %s", exc)
            self._search_service = None

        return self._search_service

    def _get_llm_analyzer(self):
        """惰性初始化 LLM 分析器。"""
        if self._llm_analyzer is not None:
            return self._llm_analyzer

        try:
            from src.analyzer import GeminiAnalyzer

            self._llm_analyzer = GeminiAnalyzer(config=self.config)

            if self._llm_analyzer.is_available():
                logger.info("[LLM] 分析器初始化完成")
            else:
                logger.warning("[LLM] 分析器初始化后不可用，请检查 API Key 配置")
                self._llm_analyzer = None
        except Exception as exc:
            logger.warning("[LLM] 分析器初始化失败: %s", exc)
            self._llm_analyzer = None

        return self._llm_analyzer

    # ──────────────────────────────────────────────
    # 主入口
    # ──────────────────────────────────────────────

    def run(self, industry_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        执行行业深度分析。

        Args:
            industry_id: 行业 ID。为 None 时分析所有配置的行业。

        Returns:
            每个行业的分析报告列表。
        """
        industries = self._load_industries()

        if industry_id:
            industries = [i for i in industries if i["id"] == industry_id]
            if not industries:
                logger.error("未找到行业: %s", industry_id)
                available = [i["id"] for i in self._load_industries()]
                logger.info("可用行业: %s", available)
                return []

        logger.info("[IndustryPipeline] 开始分析 %d 个行业", len(industries))
        logger.info("并发控制: max_concurrency=%d, request_delay=%.1fs", self.concurrency, self.request_delay)

        return asyncio.run(self._analyze_all_industries(industries))

    # ──────────────────────────────────────────────
    # 配置加载
    # ──────────────────────────────────────────────

    @staticmethod
    def _load_industries() -> List[Dict[str, Any]]:
        """从 config/industries.yaml 加载行业配置。"""
        import yaml
        from pathlib import Path

        config_path = Path(__file__).resolve().parent.parent / "config" / "industries.yaml"

        if not config_path.exists():
            logger.error("行业配置文件不存在: %s", config_path)
            return []

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        return data.get("industries", [])

    # ──────────────────────────────────────────────
    # 异步批量分析
    # ──────────────────────────────────────────────

    async def _analyze_all_industries(
        self, industries: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """异步并发分析所有行业。"""
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _analyze_one(industry: Dict[str, Any]) -> Dict[str, Any]:
            async with semaphore:
                return await self._analyze_industry_async(industry)

        tasks = [_analyze_one(ind) for ind in industries]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 过滤异常
        output = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.error(
                    "行业 '%s' 分析异常: %s",
                    industries[i].get("name", "?"),
                    r,
                )
                output.append({
                    "industry": industries[i],
                    "error": str(r),
                    "status": "failed",
                })
            else:
                output.append(r)

        return output

    async def _analyze_industry_async(
        self, industry: Dict[str, Any]
    ) -> Dict[str, Any]:
        """异步分析单个行业（Top-Down 三步法）。"""
        industry_name = industry["name"]
        stocks = industry.get("stocks", [])

        logger.info("=" * 60)
        logger.info("[IndustryPipeline] 开始分析行业: %s (%d 只成分股)", industry_name, len(stocks))
        logger.info("=" * 60)

        # ── Step 1: 宏观分析 ──
        logger.info("[Step 1/3] 宏观分析: %s", industry_name)
        macro = await self._macro_analysis_async(industry)

        # ── Step 2: 微观下钻 ──
        logger.info("[Step 2/3] 成分股分析: %s (%d 只)", industry_name, len(stocks))
        stock_analyses = await self._micro_analysis_async(stocks)

        success_count = sum(1 for s in stock_analyses if s.get("status") != "failed")
        logger.info(
            "[Step 2/3] 完成: %d/%d 只成功",
            success_count,
            len(stocks),
        )

        # ── Step 3: CIO Agent 聚合 ──
        logger.info("[Step 3/3] CIO Agent 聚合: %s", industry_name)
        cio_report = await self._cio_aggregate_async(industry, macro, stock_analyses)

        logger.info("[IndustryPipeline] 行业 '%s' 分析完成", industry_name)
        return {
            "industry": industry,
            "macro_analysis": macro,
            "stock_analyses": stock_analyses,
            "cio_report": cio_report,
            "timestamp": _utc_now_iso(),
        }

    # ──────────────────────────────────────────────
    # Step 1: 行业宏观分析
    # ──────────────────────────────────────────────

    async def _macro_analysis_async(self, industry: Dict[str, Any]) -> Dict[str, Any]:
        """
        搜索行业新闻 → LLM 生成行业景气度与宏观逻辑分析。

        容错: 搜索失败或 LLM 不可用时返回基础骨架。
        """
        keywords = industry.get("keywords", [industry["name"]])
        industry_name = industry["name"]

        # ── 1a. 搜索行业新闻 ──
        news_text = await self._search_industry_news(keywords, industry_name)

        # ── 1b. 调用 LLM 生成宏观分析 ──
        analyzer = self._get_llm_analyzer()
        if analyzer is None:
            logger.warning("[宏观] LLM 不可用，返回骨架")
            return self._fallback_macro(industry_name, news_text)

        try:
            prompt = self._build_macro_prompt(industry, news_text)
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                analyzer.generate_text,
                prompt,
                MACRO_MAX_TOKENS,
                MACRO_TEMPERATURE,
            )

            if response:
                parsed = _parse_llm_json(response)
                if parsed:
                    logger.info("[宏观] '%s' 分析完成: outlook=%s", industry_name, parsed.get("outlook", "?"))
                    return parsed

        except Exception as exc:
            logger.warning("[宏观] '%s' LLM 调用失败: %s", industry_name, exc)

        return self._fallback_macro(industry_name, news_text)

    async def _search_industry_news(
        self, keywords: List[str], industry_name: str
    ) -> str:
        """搜索行业相关新闻，返回格式化的新闻摘要文本。"""
        search_service = self._get_search_service()
        if search_service is None or not search_service.is_available:
            logger.warning("[搜索] 不可用，跳过行业新闻搜索")
            return ""

        # 构建搜索查询: 用第一个关键词，组合"行业"+"最新动态"
        primary_kw = keywords[0] if keywords else industry_name
        query = f"{primary_kw} 行业 最新动态 前景"

        try:
            logger.info("[搜索] 搜索行业新闻: '%s'", query)

            # 使用 SearchService.search_stock_news() 公共接口
            # 传入 focus_keywords，SearchService 会直接用关键词构建查询
            loop = asyncio.get_event_loop()
            search_response = await loop.run_in_executor(
                None,
                search_service.search_stock_news,
                "",              # stock_code (行业无代码，传空)
                industry_name,   # stock_name
                10,              # max_results
                keywords,        # focus_keywords
            )

            if search_response and search_response.success and getattr(search_response, "results", None):
                news_items = []
                for r in search_response.results[:10]:
                    title = getattr(r, "title", "") or ""
                    snippet = getattr(r, "snippet", "") or ""
                    published = getattr(r, "published_date", "") or ""
                    if title or snippet:
                        date_prefix = f"[{published}] " if published else ""
                        news_items.append(f"- {date_prefix}{title}: {snippet[:200]}")

                if news_items:
                    result = "\n".join(news_items)
                    logger.info("[搜索] 获取到 %d 条行业新闻", len(news_items))
                    return result

            logger.info("[搜索] 未找到 '%s' 的相关新闻", industry_name)
            return ""

        except Exception as exc:
            logger.warning("[搜索] '%s' 新闻搜索失败: %s", industry_name, exc)
            return ""

    def _build_macro_prompt(
        self, industry: Dict[str, Any], news_text: str
    ) -> str:
        """构建行业宏观分析 LLM Prompt。"""
        industry_name = industry["name"]
        keywords = industry.get("keywords", [])
        stocks = industry.get("stocks", [])

        stock_list = "\n".join(
            f"- {s['name']}({s['code']}) [{s.get('role', '')}]" for s in stocks
        )

        prompt = f"""{INDUSTRY_MACRO_SYSTEM_PROMPT}

---

## 📊 行业基本信息

- **行业名称**: {industry_name}
- **关键词**: {", ".join(keywords)}
- **成分股**:
{stock_list}

---

## 📰 行业最新动态

"""
        if news_text:
            prompt += news_text
        else:
            prompt += "（未搜索到相关新闻，请基于行业知识给出分析）"

        prompt += """

---

请根据以上信息，输出 JSON 格式的行业宏观分析报告。"""

        return prompt

    def _fallback_macro(
        self, industry_name: str, news_text: str
    ) -> Dict[str, Any]:
        """LLM 不可用时的宏观分析兜底。"""
        return {
            "status": "fallback",
            "sentiment_summary": f"行业 '{industry_name}' 宏观分析待 LLM 可用后生成",
            "outlook": "neutral",
            "confidence": 0.0,
            "core_logic": "",
            "risk_factors": [],
            "positive_catalysts": [],
            "key_events": [],
            "policy_impact": "",
            "cycle_position": "",
            "competitive_landscape": "",
            "news_raw": news_text[:500] if news_text else "",
        }

    # ──────────────────────────────────────────────
    # Step 2: 成分股微观分析
    # ──────────────────────────────────────────────

    async def _micro_analysis_async(
        self, stocks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        并发分析行业成分股。

        每只成分股调用 SingleStockPipeline.analyze_single()，
        由 asyncio.Semaphore 控制并发数，request_delay 控制请求间隔。
        """
        sp = self._get_single_stock_pipeline()
        sem = asyncio.Semaphore(self.concurrency)
        results = []

        async def _analyze_one(stock: Dict[str, Any], index: int) -> Dict[str, Any]:
            async with sem:
                code = stock["code"]
                name = stock["name"]
                role = stock.get("role", "")

                try:
                    logger.info(
                        "  [%d/%d] 分析 %s(%s) [%s]",
                        index + 1,
                        len(stocks),
                        name,
                        code,
                        role,
                    )

                    # 调用单股管线（同步代码在 executor 中执行）
                    loop = asyncio.get_event_loop()
                    summary = await loop.run_in_executor(
                        None,
                        sp.analyze_single,
                        code,
                    )

                    summary["role"] = role
                    return summary

                except Exception as exc:
                    logger.warning(
                        "  [失败] %s(%s): %s", name, code, exc
                    )
                    return {
                        "code": code,
                        "name": name,
                        "role": role,
                        "error": str(exc),
                        "status": "failed",
                    }

            # 请求间隔 — 在 semaphore 内部释放后等待
            await asyncio.sleep(self.request_delay)

        tasks = [_analyze_one(stock, i) for i, stock in enumerate(stocks)]
        raw_results = await asyncio.gather(*tasks)
        return list(raw_results)

    # ──────────────────────────────────────────────
    # Step 3: CIO Agent 聚合
    # ──────────────────────────────────────────────

    # CIO Agent 输出 Token
    CIO_MAX_TOKENS = 4096
    # CIO 分析温度（低温度确保逻辑严谨）
    CIO_TEMPERATURE = 0.3
    # 单股摘要最大字符数（输入 CIO 时）— 防止 Token 爆炸
    CIO_STOCK_MAX_CHARS = 300
    # 最大成分股数量（超出部分只统计不计入详细分析）
    CIO_MAX_STOCKS_IN_PROMPT = 10

    _CIO_SYSTEM_PROMPT = """你是一位资深首席投资官 (CIO)。你的任务是基于行业宏观分析
和多家成分股的微观表现，生成一份结构化的行业投资策略报告。

请严格按以下 JSON 格式输出，不要输出其他内容：
{
  "chain_analysis": "<产业链整体点评：上下游景气度、产能周期、利润分配格局>",
  "leader_analysis": "<龙头企业深度分析：竞争优势、市场份额、估值对比>",
  "allocation_advice": "<配置建议：超配/标配/低配，以及优先级排序>",
  "risk_warning": "<行业风险提示：政策风险、技术风险、需求风险等>",
  "comparative_insight": "<横向对比洞察：不同企业的差异化特征和投资价值排序>",
  "composite_signal": "bullish" | "bearish" | "neutral",
  "composite_confidence": 0.0~1.0,
  "key_themes": ["<主题1>", "<主题2>", "<主题3>"]
}

注意：
- 基于提供的宏观和微观数据进行分析，不要凭空编造
- 横向对比要给出一二三梯队的明确排序及理由
- 风险提示要具体，不要泛泛而谈
- allocation_advice 要给出超配/标配/低配的具体标的建议"""

    async def _cio_aggregate_async(
        self,
        industry: Dict[str, Any],
        macro: Dict[str, Any],
        stock_analyses: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        CIO Agent 聚合 — 将行业宏观 + 多只成分股微观合并，
        调用 LLM 输出结构化行业全景研报。

        Token 管理策略:
        1. 单股摘要截断到 CIO_STOCK_MAX_CHARS
        2. 超出 CIO_MAX_STOCKS_IN_PROMPT 的成分股仅统计
        3. 超出上下文时进一步截断宏观分析中的长文本
        """
        industry_name = industry["name"]

        # ── 统计 ──
        total = len(stock_analyses)
        success_count = sum(1 for s in stock_analyses if s.get("status") != "failed")
        bullish = sum(1 for s in stock_analyses if s.get("signal") in ("strong_buy", "buy"))
        bearish = sum(1 for s in stock_analyses if s.get("signal") in ("strong_sell", "sell"))
        neutral = total - bullish - bearish
        failed = total - success_count

        # ── Token 管理: 截断单股摘要 ──
        trimmed_analyses = []
        for s in stock_analyses:
            trimmed = dict(s)
            summary = str(s.get("summary", ""))
            if len(summary) > self.CIO_STOCK_MAX_CHARS:
                trimmed["summary"] = summary[:self.CIO_STOCK_MAX_CHARS] + "…"
            trimmed_analyses.append(trimmed)

        # 超出上限的成分股只统计
        detailed_stocks = trimmed_analyses[:self.CIO_MAX_STOCKS_IN_PROMPT]
        overflow = total - len(detailed_stocks)

        # ── 估算 Token ──
        input_chars = sum(
            len(str(s.get("summary", ""))) for s in detailed_stocks
        )
        input_chars += len(str(macro.get("core_logic", "")))
        input_chars += len(str(macro.get("sentiment_summary", "")))
        estimated_tokens = max(1, input_chars // 3)

        logger.info(
            "[CIO Agent] '%s': %d 只成分股 (bullish=%d bearish=%d neutral=%d failed=%d), "
            "详细分析 %d 只, 估算输入 tokens=%d",
            industry_name, total, bullish, bearish, neutral, failed,
            len(detailed_stocks), estimated_tokens,
        )

        if overflow > 0:
            logger.info("[CIO Agent] %d 只成分股超出上限，仅统计不计入详细分析", overflow)

        # ── 尝试 LLM 调用 ──
        analyzer = self._get_llm_analyzer()
        if analyzer is not None:
            try:
                prompt = self._build_cio_prompt(
                    industry, macro, detailed_stocks, overflow,
                    bullish, bearish, neutral, failed,
                )
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    analyzer.generate_text,
                    prompt,
                    self.CIO_MAX_TOKENS,
                    self.CIO_TEMPERATURE,
                )

                if response:
                    parsed = _parse_llm_json(response)
                    if parsed:
                        parsed["summary_stats"] = {
                            "total": total,
                            "success": success_count,
                            "bullish": bullish,
                            "bearish": bearish,
                            "neutral": neutral,
                            "failed": failed,
                        }
                        parsed["token_usage"] = {
                            "estimated_input_tokens": estimated_tokens,
                        }
                        logger.info(
                            "[CIO Agent] '%s' 聚合完成: signal=%s",
                            industry_name,
                            parsed.get("composite_signal", "?"),
                        )
                        return parsed

            except Exception as exc:
                logger.warning("[CIO Agent] '%s' LLM 调用失败: %s", industry_name, exc)

        # ── 兜底: 基于统计生成骨架 ──
        return self._cio_fallback(
            industry_name, total, success_count,
            bullish, bearish, neutral, failed,
            estimated_tokens,
        )

    def _build_cio_prompt(
        self,
        industry: Dict[str, Any],
        macro: Dict[str, Any],
        detailed_stocks: List[Dict[str, Any]],
        overflow: int,
        bullish: int,
        bearish: int,
        neutral: int,
        failed: int,
    ) -> str:
        """构建 CIO Agent 输入 Prompt。"""
        industry_name = industry["name"]
        total = len(detailed_stocks) + overflow + failed

        # 宏观部分
        macro_section = f"""## 行业宏观分析

- **景气度**: {macro.get('sentiment_summary', '未知')}
- **展望**: {macro.get('outlook', 'neutral')}
- **核心逻辑**: {macro.get('core_logic', '未知')}
- **政策**: {macro.get('policy_impact', '未知')}
- **周期位置**: {macro.get('cycle_position', '未知')}
- **竞争格局**: {macro.get('competitive_landscape', '未知')}
"""

        # 微观部分
        stock_lines = ["## 成分股微观表现\n"]
        stock_lines.append(f"| # | 企业 | 角色 | 信号 | 评分 | 核心摘要 |")
        stock_lines.append(f"|---|------|------|------|------|----------|")

        for i, s in enumerate(detailed_stocks, 1):
            name = s.get("name", "?")
            code = s.get("code", "?")
            role = s.get("role", "")
            signal = s.get("signal", "neutral")
            score = s.get("score", 0.5)
            summary = (s.get("summary", "") or "")[:150]

            signal_icon = {"strong_buy": "🔴", "buy": "🟢", "hold": "🟡", "sell": "🔵", "strong_sell": "⚫"}.get(signal, "⚪")
            stock_lines.append(
                f"| {i} | {name}({code}) | {role} | {signal_icon}{signal} | {score:.2f} | {summary} |"
            )

        if overflow > 0:
            stock_lines.append(f"\n> ⚠️ 另有 {overflow} 只成分股因超出上限，仅统计未详细列出。")

        # 统计部分
        stats_section = f"""## 统计概览

- 成分股总数: {total}
- 成功分析: {total - failed}
- 看多 (buy/strong_buy): {bullish}
- 看空 (sell/strong_sell): {bearish}
- 中性 (hold): {neutral}
- 失败: {failed}
"""

        # 构建股票表格文本
        stock_table = "\n".join(stock_lines)

        final_prompt = (
            self._CIO_SYSTEM_PROMPT
            + "\n\n---\n\n"
            + macro_section
            + "\n\n---\n\n"
            + stats_section
            + "\n\n---\n\n"
            + stock_table
            + "\n\n---\n\n"
            + "请根据以上信息，输出 JSON 格式的 CIO 行业投资策略报告。"
        )
        return final_prompt

    def _cio_fallback(
        self,
        industry_name: str,
        total: int,
        success: int,
        bullish: int,
        bearish: int,
        neutral: int,
        failed: int,
        estimated_tokens: int,
    ) -> Dict[str, Any]:
        """CIO Agent 不可用时的兜底输出。"""
        return {
            "status": "fallback",
            "summary_stats": {
                "total": total,
                "success": success,
                "bullish": bullish,
                "bearish": bearish,
                "neutral": neutral,
                "failed": failed,
            },
            "chain_analysis": f"行业 '{industry_name}' CIO 聚合分析待 LLM 可用后生成",
            "leader_analysis": "",
            "allocation_advice": "",
            "risk_warning": "",
            "comparative_insight": "",
            "composite_signal": "neutral",
            "composite_confidence": 0.0,
            "key_themes": [],
            "token_usage": {
                "estimated_input_tokens": estimated_tokens,
            },
        }

    # ──────────────────────────────────────────────
    # 报告渲染
    # ──────────────────────────────────────────────

    @staticmethod
    def render_report(
        industry: Dict[str, Any],
        macro: Dict[str, Any],
        stock_analyses: List[Dict[str, Any]],
        cio: Dict[str, Any],
    ) -> str:
        """
        将行业分析结果渲染为 Markdown 报告。

        优先使用 Jinja2 模板，失败时回退到纯文本。
        """
        from datetime import datetime

        report_date = datetime.now().strftime("%Y-%m-%d")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        industry_name = industry.get("name", "")

        total = len(stock_analyses)
        success_count = sum(1 for s in stock_analyses if s.get("status") != "failed")

        # 信号图标映射
        signal_icons = {
            "strong_buy": "🔴 强烈看多",
            "buy": "🟢 看多",
            "hold": "🟡 持有",
            "sell": "🔵 看空",
            "strong_sell": "⚫ 强烈看空",
        }

        # 宏观展望图标
        outlook_map = {
            "bullish": "🟢 看好",
            "bearish": "🔴 看淡",
            "neutral": "🟡 中性",
        }

        # 准备模板变量
        enriched_stocks = []
        bullish_count = 0
        bearish_count = 0
        neutral_count = 0

        for s in stock_analyses:
            signal = s.get("signal", "neutral")
            if signal in ("strong_buy", "buy"):
                bullish_count += 1
            elif signal in ("strong_sell", "sell"):
                bearish_count += 1
            else:
                neutral_count += 1

            risk_flags = s.get("risk_flags", []) or []
            risk_text = "; ".join(risk_flags[:3]) if risk_flags else "—"

            enriched_stocks.append({
                "name": s.get("name", "?"),
                "code": s.get("code", "?"),
                "role": s.get("role", ""),
                "signal": signal,
                "signal_icon": signal_icons.get(signal, "⚪ 未知"),
                "score": f"{s.get('score', 0.5):.2f}",
                "summary": s.get("summary", ""),
                "detail": (
                    f"**信号**: {signal_icons.get(signal, signal)} | "
                    f"**评分**: {s.get('score', 0.5):.2f} | "
                    f"**置信度**: {s.get('confidence', '—')}\n\n"
                    f"**摘要**: {s.get('summary', '—')[:200]}\n\n"
                    f"**风险**: {risk_text}"
                ),
            })

        context = {
            "report_date": report_date,
            "timestamp": timestamp,
            "industry_name": industry_name,
            "total_stocks": total,
            "success_count": success_count,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "neutral_count": neutral_count,
            "macro": {
                **macro,
                "outlook_label": outlook_map.get(macro.get("outlook", "neutral"), "中性"),
            },
            "stocks": enriched_stocks,
            "cio": cio,
        }

        # 尝试 Jinja2 渲染
        try:
            from jinja2 import Environment, FileSystemLoader
            from pathlib import Path

            templates_dir = Path(__file__).resolve().parent.parent / "templates"
            if (templates_dir / "report_industry.j2").exists():
                env = Environment(loader=FileSystemLoader(str(templates_dir)))
                template = env.get_template("report_industry.j2")
                rendered = template.render(**context)
                logger.info("[渲染] 行业报告 Jinja2 渲染完成 (%d 字符)", len(rendered))
                return rendered
        except Exception as exc:
            logger.warning("[渲染] Jinja2 渲染失败，使用纯文本回退: %s", exc)

        # 纯文本回退
        return _render_industry_fallback(context)

    # ──────────────────────────────────────────────
    # HTML 渲染
    # ──────────────────────────────────────────────

    @staticmethod
    def render_html(
        industry: Dict[str, Any],
        macro: Dict[str, Any],
        stock_analyses: List[Dict[str, Any]],
        cio: Dict[str, Any],
    ) -> Optional[str]:
        """
        将行业分析结果渲染为自包含的 HTML 报告。

        使用 templates/report_industry.html.j2 模板。
        返回 HTML 字符串，失败时返回 None。
        """
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        industry_name = industry.get("name", "")
        total = len(stock_analyses)
        success_count = sum(1 for s in stock_analyses if s.get("status") != "failed")

        signal_labels = {
            "strong_buy": "强烈看多",
            "buy": "看多",
            "hold": "持有",
            "sell": "看空",
            "strong_sell": "强烈看空",
        }

        outlook_map = {
            "bullish": "看好",
            "bearish": "看淡",
            "neutral": "中性",
        }

        enriched_stocks = []
        bullish_count = 0
        bearish_count = 0
        neutral_count = 0

        for s in stock_analyses:
            signal = s.get("signal", "neutral")
            if signal in ("strong_buy", "buy"):
                bullish_count += 1
            elif signal in ("strong_sell", "sell"):
                bearish_count += 1
            else:
                neutral_count += 1

            risk_flags = s.get("risk_flags", []) or []

            enriched_stocks.append({
                "name": s.get("name", "?"),
                "code": s.get("code", "?"),
                "role": s.get("role", ""),
                "signal": signal,
                "score": s.get("score", 0.5),
                "summary": s.get("summary", ""),
                "key_metrics": s.get("key_metrics", {}),
                "risk_flags": risk_flags,
                "sniper_points": s.get("sniper_points", {}),
            })

        context = {
            "timestamp": timestamp,
            "industry_name": industry_name,
            "total_stocks": total,
            "success_count": success_count,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "neutral_count": neutral_count,
            "signal_labels": signal_labels,
            "macro": {
                **macro,
                "outlook_label": outlook_map.get(macro.get("outlook", "neutral"), "中性"),
            },
            "stocks": enriched_stocks,
            "cio": cio,
        }

        try:
            from jinja2 import Environment, FileSystemLoader
            from pathlib import Path

            templates_dir = Path(__file__).resolve().parent.parent / "templates"
            template_path = templates_dir / "report_industry.html.j2"
            if not template_path.exists():
                logger.warning("[HTML] 模板不存在: %s", template_path)
                return None

            env = Environment(loader=FileSystemLoader(str(templates_dir)))
            template = env.get_template("report_industry.html.j2")
            rendered = template.render(**context)
            logger.info("[HTML] 报告渲染完成 (%d 字符)", len(rendered))
            return rendered
        except Exception as exc:
            logger.warning("[HTML] 渲染失败: %s", exc)
            return None

    # ──────────────────────────────────────────────
    # 对外接口
    # ──────────────────────────────────────────────

    def get_notifier(self):
        """获取通知服务实例。"""
        from src.notification import NotificationService

        return NotificationService()

    def push_report(
        self,
        industry: Dict[str, Any],
        report_text: str,
    ) -> bool:
        """推送行业研报到所有已配置渠道。"""
        try:
            notifier = self.get_notifier()
            if not notifier.is_available():
                logger.warning("[推送] 无可用通知渠道，跳过推送")
                return False

            success = notifier.send(report_text)
            if success:
                logger.info("[推送] 行业 '%s' 报告已推送", industry.get("name", ""))
            else:
                logger.warning("[推送] 行业 '%s' 报告推送失败", industry.get("name", ""))
            return success
        except Exception as exc:
            logger.warning("[推送] 异常: %s", exc)
            return False


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────


def _parse_llm_json(text: str) -> Optional[Dict[str, Any]]:
    """解析 LLM 返回的 JSON 字符串。"""
    import json

    if not text:
        return None

    text = text.strip()

    # 去除可能的 markdown 代码块标记
    if text.startswith("```"):
        lines = text.split("\n")
        # 去掉首行 ```json 和末行 ```
        if len(lines) > 2:
            text = "\n".join(lines[1:-1])

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 尝试提取 {} 之间的内容
        import re
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return None


def _utc_now_iso() -> str:
    """当前 UTC 时间 ISO 格式字符串。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _render_industry_fallback(context: dict) -> str:
    """纯文本行业报告回退渲染（不依赖 Jinja2）。"""
    lines = [
        f"# 📊 {context['industry_name']} 行业深度分析",
        f"> 分析时间: {context['timestamp']} | 成分股: {context['total_stocks']} 只",
        "",
        "---",
        "",
        "## 1. 行业宏观动向",
        "",
    ]

    macro = context.get("macro", {})
    if macro.get("sentiment_summary"):
        lines.append(f"**景气度判断**: {macro.get('outlook_label', '')} | {macro['sentiment_summary']}")
        lines.append("")
    if macro.get("core_logic"):
        lines.append(f"**核心驱动逻辑**: {macro['core_logic']}")
        lines.append("")
    if macro.get("risk_factors"):
        lines.append("**⚠️ 风险因素**:")
        for r in macro["risk_factors"]:
            lines.append(f"- {r}")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## 2. 核心企业微观表现",
        "",
        f"| 企业 | 角色 | 信号 | 评分 |",
        "|------|------|------|------|",
    ])

    for s in context.get("stocks", []):
        lines.append(
            f"| {s['name']}({s['code']}) | {s['role']} | {s['signal_icon']} | {s['score']} |"
        )

    lines.extend([
        "",
        f"**统计**: 总量 {context['total_stocks']} | "
        f"看多 {context['bullish_count']} | "
        f"看空 {context['bearish_count']} | "
        f"中性 {context['neutral_count']}",
        "",
        "---",
        "",
        "## 3. AI 综合投资策略",
        "",
    ])

    cio = context.get("cio", {})
    if cio.get("chain_analysis"):
        lines.append(f"**产业链点评**: {cio['chain_analysis']}")
        lines.append("")
    if cio.get("allocation_advice"):
        lines.append(f"**配置建议**: {cio['allocation_advice']}")
        lines.append("")
    if cio.get("risk_warning"):
        lines.append(f"**⚠️ 风险提示**: {cio['risk_warning']}")
        lines.append("")

    lines.extend([
        "---",
        "*本报告由 AI 行业分析管线自动生成，仅供参考。*",
    ])

    return "\n".join(lines)
