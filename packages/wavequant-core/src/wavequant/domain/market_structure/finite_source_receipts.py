"""当前不可变来源的有限性收据；只复用已经检查过的真实对象根。"""

from sys import getsizeof
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .owned_evidence import CertifiedSource, OwnedEvidence


class FiniteSourceReceipts:
    """收据只覆盖当前来源持有的点及直接字段，额度来自原来源输入缓存。

    节点表强引用防止 id 重用，但所有节点都可从已登记的当前点到达；移除
    点时同步撤销引用，不积累旧版本。只有生产者已承诺整图不可变、且原
    finite 检查成功后才登记；未认证可变图不能借用这些结论。不可变旧图
    不可能在登记后新增回到新尾点的边，因此跳过旧根不会隐藏新环。
    """

    def __init__(self, owner: 'OwnedEvidence') -> None:
        self.owner = owner
        self.token = object()
        self.blocked = False
        self._points: dict[int, tuple[dict[str, object], tuple[int, ...], int]] = {}
        self._nodes: dict[int, tuple[object, int]] = {}
        self._point_bytes = 0
        self._node_bytes = 0
        self._checked: object | None = None
        self.stats = dict(checks=0, root_hits=0, subtree_skips=0, registered=0,
                          rejections=0, budget_rejections=0, peak_memory_bytes=self.memory_used)

    @property
    def memory_used(self) -> int:
        return self._point_bytes + self._node_bytes + getsizeof(self._points) + getsizeof(self._nodes) + 4096

    def clear(self, *, blocked: bool = False) -> None:
        self._points = {}
        self._nodes = {}
        self._point_bytes = self._node_bytes = 0
        self._checked = None
        self.token = object()
        self.blocked = blocked

    def fit(self, limit: int) -> bool:
        # 业务来源优先占原额度；可选收据被撤销后不逐日重拍同一大图。
        if self.memory_used > limit:
            self.stats['budget_rejections'] += 1
            self.stats['rejections'] += 1
            self.clear(blocked=True)
        # 连固定外壳都无法容纳时，调用方撤销本对象并只留下禁用标记。
        return self.memory_used <= limit

    def knows(self, value: object, owner: 'OwnedEvidence') -> bool:
        if owner is not self.owner or owner._closed:
            return False
        entry = self._nodes.get(id(value))
        return entry is not None and entry[0] is value

    def check(self, source: 'CertifiedSource', position: int) -> bool:
        """原 finite 循环校验新图；只为本次实际通过的内部点签发登记资格。"""
        from .owned_evidence import CertifiedSource

        self._checked = None
        self.stats['checks'] += 1
        point = source[position]
        certified = (type(source) is CertifiedSource and source._sealed
                     and source.owner is self.owner and not self.owner._closed)
        valid = self.owner.finite(point, _receipts=self if certified else None)
        if valid and certified and type(point) is dict:
            self._checked = point
        if not valid:
            self.stats['rejections'] += 1
        return valid

    def retain(self, source: 'CertifiedSource', position: int, *, limit: int) -> object | None:
        """只登记刚通过检查的点，或已登记根本身；签名字节不构成有限性证明。"""
        from .owned_evidence import CertifiedSource
        from .trend_point_version import PointVersion

        point = source[position]
        checked = self._checked is point
        self._checked = None
        if self.memory_used > limit:
            self.fit(limit)
        if (self.blocked or type(source) is not CertifiedSource or not source._sealed
                or source.owner is not self.owner or self.owner._closed):
            self.stats['rejections'] += 1
            return None
        if type(point) is not dict or not (checked or self.knows(point, self.owner)):
            self.stats['rejections'] += 1
            return None
        identity = id(point)
        saved = self._points.get(identity)
        if saved is not None and saved[0] is point:
            self._points[identity] = point, saved[1], saved[2] + 1
            return self.token
        roots: dict[int, object] = {identity: point}
        for value in point.values():
            if type(value) in (dict, list, tuple, PointVersion, CertifiedSource):
                roots.setdefault(id(value), value)
        identities = tuple(roots)
        entry = point, identities, 1
        # 不同点的 id 整数即便值相同也可能是独立对象，逐收据保守计费。
        point_bytes = (getsizeof(identity) + getsizeof(entry) + getsizeof(identities) + getsizeof(1)
                       + sum(getsizeof(key) for key in identities))
        missing = [key for key in roots if key not in self._nodes]
        node_bytes = sum(getsizeof(key) + getsizeof((roots[key], 1)) + getsizeof(1) for key in missing)
        # Python dict 可能一次扩容；预留两个当前表和新槽的上界，在触顶前
        # 停止新收据，不插入后清空整个已验证前缀造成逐日重建。
        upper = (self.memory_used + point_bytes + node_bytes
                 + 2 * (getsizeof(self._points) + getsizeof(self._nodes)) + 256 * (len(missing) + 1))
        if upper > limit:
            self.blocked = True
            self.stats['budget_rejections'] += 1
            self.stats['rejections'] += 1
            return None
        for key, value in roots.items():
            known = self._nodes.get(key)
            self._nodes[key] = value, known[1] + 1 if known is not None else 1
        self._points[identity] = entry
        self._point_bytes += point_bytes
        self._node_bytes += node_bytes
        self.stats['peak_memory_bytes'] = max(self.stats['peak_memory_bytes'], self.memory_used)
        if self.memory_used > limit:
            self.stats['budget_rejections'] += 1
            self.stats['rejections'] += 1
            self.clear(blocked=True)
            return None
        self.stats['registered'] += 1
        return self.token

    def release(self, point: object, token: object | None) -> None:
        if token is not self.token:
            return
        identity = id(point)
        saved = self._points.get(identity)
        if saved is None or saved[0] is not point:
            return
        if saved[2] > 1:
            self._points[identity] = saved[0], saved[1], saved[2] - 1
            return
        del self._points[identity]
        self._point_bytes -= (getsizeof(identity) + getsizeof(saved) + getsizeof(saved[1]) + getsizeof(1)
                              + sum(getsizeof(key) for key in saved[1]))
        for key in saved[1]:
            value, count = self._nodes[key]
            if count > 1:
                self._nodes[key] = value, count - 1
            else:
                del self._nodes[key]
                self._node_bytes -= getsizeof(key) + getsizeof((value, 1)) + getsizeof(1)
        if not self._points:
            self._nodes = {}
            self._points = {}
            self._point_bytes = self._node_bytes = 0
