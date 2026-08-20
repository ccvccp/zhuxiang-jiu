"""订单结算业务:订单创建"""

from datetime import datetime

from core.helpers import ts
from repositories.order_repository import OrderRepository


class CheckoutService:
    def __init__(self, order_repo: OrderRepository = OrderRepository()):
        self.order_repo = order_repo

    async def submit(self, items: list, consignee, payment) -> dict:
        """订单结算提交(无并发风险:订单 ID 基于时间戳唯一)"""
        order_id = f"ZX{int(datetime.now().timestamp() * 1000) % 1000000:06d}"
        order = {
            "orderId": order_id,
            "items": items,
            "consignee": consignee,
            "payment": payment,
            "status": "pending",
            "createdAt": ts(),
        }
        await self.order_repo.create(order)
        return {
            "success": True,
            "orderId": order_id,
            "status": "pending",
            "message": f"订单 {order_id} 创建成功",
        }
