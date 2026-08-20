"""库存业务:扣减/回补

并发安全: 涉及 check-then-act + RMW,使用 stock:{productId} 锁
库存不足时返回 success=False(业务规则),不抛异常
"""

from datetime import datetime

from core.locks import get_lock
from repositories.inventory_repository import InventoryRepository


def _tx_id() -> str:
    """生成交易 ID"""
    return f"TX{int(datetime.now().timestamp() * 1000) % 1000000:06d}"


class InventoryService:
    def __init__(self, inventory_repo: InventoryRepository = InventoryRepository()):
        self.inventory_repo = inventory_repo

    async def deduct(self, product_id, quantity: int) -> dict:
        """库存扣减

        Returns:
            {success, productId, stockAfter, txId} 或 {success: False, error}

        Raises:
            KeyError: 产品不存在
        """
        async with get_lock(f"stock:{product_id}"):
            product = await self.inventory_repo.get(product_id)
            if not product:
                raise KeyError(f"产品 {product_id} 不存在")
            if product["stock"] < quantity:
                return {"success": False,
                        "error": f"库存不足: 当前 {product['stock']}, 需要 {quantity}"}
            stock_after = await self.inventory_repo.deduct(product_id, quantity)
            return {
                "success": True,
                "productId": product_id,
                "stockAfter": stock_after,
                "txId": _tx_id(),
            }

    async def restock(self, product_id, quantity: int) -> dict:
        """库存回补

        Raises:
            KeyError: 产品不存在
        """
        async with get_lock(f"stock:{product_id}"):
            product = await self.inventory_repo.get(product_id)
            if not product:
                raise KeyError(f"产品 {product_id} 不存在")
            stock_after = await self.inventory_repo.restock(product_id, quantity)
            return {
                "success": True,
                "productId": product_id,
                "stockAfter": stock_after,
                "txId": _tx_id(),
            }
