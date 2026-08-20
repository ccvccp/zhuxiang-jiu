"""仓储 Repository

封装 _mock_store["warehouse"] 的访问:
    - slots: 库位映射 {slot: productId | None}
    - inbound_log / outbound_log: 出入库日志列表
"""

from typing import Optional

from repositories.store import _mock_store


class WarehouseRepository:
    """仓储数据访问"""

    def __init__(self, store: dict = _mock_store):
        self.store = store

    @property
    def _warehouse(self) -> dict:
        return self.store["warehouse"]

    def get_slots(self) -> dict:
        """返回所有库位映射"""
        return self._warehouse["slots"]

    def append_inbound_log(self, log: dict) -> dict:
        """追加入库日志"""
        self._warehouse["inbound_log"].append(log)
        return log

    def append_outbound_log(self, log: dict) -> dict:
        """追加出库日志"""
        self._warehouse["outbound_log"].append(log)
        return log

    def count_inbound(self) -> int:
        """入库日志条数"""
        return len(self._warehouse["inbound_log"])

    def count_outbound(self) -> int:
        """出库日志条数"""
        return len(self._warehouse["outbound_log"])

    def count_inbound_before(self, count: int) -> int:
        """辅助:返回当前入库条数与给定基线的差(测试用)"""
        return len(self._warehouse["inbound_log"]) - count

    def count_outbound_before(self, count: int) -> int:
        """辅助:返回当前出库条数与给定基线的差(测试用)"""
        return len(self._warehouse["outbound_log"]) - count
