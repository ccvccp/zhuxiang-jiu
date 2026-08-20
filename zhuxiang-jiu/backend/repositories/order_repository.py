"""订单 Repository

封装 _mock_store["orders"] 的访问(列表存储)。
"""

from repositories.store import _mock_store


class OrderRepository:
    """订单数据访问"""

    def __init__(self, store: dict = _mock_store):
        self.store = store

    def create(self, order: dict) -> str:
        """追加订单,返回 orderId"""
        self.store["orders"].append(order)
        return order.get("orderId")

    def count(self) -> int:
        """订单总数"""
        return len(self.store["orders"])

    def list_all(self) -> list[dict]:
        """列出所有订单"""
        return list(self.store["orders"])
