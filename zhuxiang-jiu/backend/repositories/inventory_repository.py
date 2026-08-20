"""库存 Repository

封装 _mock_store["inventory"] 的访问。
锁键: stock:{productId}(deduct/restock 共享,由 services 层负责)
"""

from typing import Optional

from repositories.store import _mock_store


class InventoryRepository:
    """库存数据访问(以 product_id 为 key,str 类型存储)"""

    def __init__(self, store: dict = _mock_store):
        self.store = store

    def get(self, product_id) -> Optional[dict]:
        """按 product_id 查询库存(product_id 内部统一转 str)"""
        return self.store["inventory"].get(str(product_id))

    def get_stock(self, product_id) -> int:
        """查询库存量,产品不存在返回 0"""
        product = self.store["inventory"].get(str(product_id))
        return product["stock"] if product else 0

    def set_stock(self, product_id, stock: int) -> int:
        """直接设置库存量(测试辅助:模拟初始库存)

        若产品不存在会自动创建条目(对齐 _mock_store["inventory"][...] = {...})
        """
        key = str(product_id)
        if key not in self.store["inventory"]:
            self.store["inventory"][key] = {"stock": 0, "reserved": 0}
        self.store["inventory"][key]["stock"] = stock
        return stock

    def deduct(self, product_id, quantity: int) -> int:
        """扣减库存,返回扣减后余额

        Raises:
            KeyError: 产品不存在
            ValueError: 库存不足
        """
        key = str(product_id)
        product = self.store["inventory"].get(key)
        if not product:
            raise KeyError(product_id)
        if product["stock"] < quantity:
            raise ValueError(f"库存不足: 当前 {product['stock']}, 需要 {quantity}")
        product["stock"] -= quantity
        return product["stock"]

    def restock(self, product_id, quantity: int) -> int:
        """回补库存,返回回补后余额

        Raises:
            KeyError: 产品不存在
        """
        key = str(product_id)
        product = self.store["inventory"].get(key)
        if not product:
            raise KeyError(product_id)
        product["stock"] += quantity
        return product["stock"]
