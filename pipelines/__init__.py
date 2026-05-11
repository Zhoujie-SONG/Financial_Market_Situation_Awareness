# -*- coding: utf-8 -*-
"""
===================================
分析管线包 (Pipelines)
===================================

职责：
- 提供面向对象的 Pipeline 抽象，封装分析流程
- 支持双轨制运行模式：
    模式 A — 单股分析 (SingleStockPipeline)
    模式 B — 行业深度分析 (IndustryPipeline)
- 所有 Pipeline 继承自统一的 BasePipeline 抽象基类
"""

from pipelines.base import BasePipeline
from pipelines.single_stock import SingleStockPipeline
from pipelines.industry import IndustryPipeline

__all__ = [
    "BasePipeline",
    "SingleStockPipeline",
    "IndustryPipeline",
]
