# -*- coding: utf-8 -*-
"""
===================================
管线抽象基类 (Base Pipeline)
===================================

所有分析管线的统一抽象接口。
管线封装了完整的"数据获取 → 分析 → 报告生成"流程。
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

from src.config import Config, get_config


class BasePipeline(ABC):
    """
    所有分析管线的抽象基类。

    子类必须实现 run() 方法，执行完整的分析流程并返回结构化结果。

    用法:
        class MyPipeline(BasePipeline):
            def run(self, **kwargs) -> Any:
                ...
    """

    def __init__(self, config: Optional[Config] = None):
        """
        初始化管线。

        Args:
            config: 配置对象。为 None 时使用全局单例配置。
        """
        self.config = config or get_config()

    @abstractmethod
    def run(self, **kwargs) -> Any:
        """
        执行分析管线。

        Args:
            **kwargs: 各管线特定的参数。

        Returns:
            管线执行结果，类型由具体子类定义。
        """
        ...
