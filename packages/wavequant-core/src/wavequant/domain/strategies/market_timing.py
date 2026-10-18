"""请求内累计的计算阶段耗时；不参与领域证据、缓存身份或交易判断。"""

from dataclasses import dataclass


@dataclass(slots=True)
class MarketTiming:
    drawing: float = 0.0
    projection: float = 0.0
    geometry: float = 0.0
    lifecycle: float = 0.0
    publication: float = 0.0
    permissions: float = 0.0

    def progress_text(self) -> str:
        """列出互不重叠的计算子阶段，账户与交易规则仍由协调器单独计时。"""
        return (f'折线构建 {self.drawing:.1f}秒，峰谷投影 {self.projection:.1f}秒，'
                f'趋势归并 {self.geometry:.1f}秒，'
                f'六态 {self.lifecycle:.1f}秒，历史保存 {self.publication:.1f}秒，'
                f'交易资格 {self.permissions:.1f}秒')
