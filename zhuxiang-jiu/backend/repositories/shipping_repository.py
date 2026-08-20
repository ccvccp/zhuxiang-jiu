"""代理商区域认领 Repository

封装 _mock_store["shipping_claims"] 的访问。
key: region(区域字符串)
value: agent_id
"""

from typing import Optional

from repositories.store import _mock_store


class ShippingClaimRepository:
    """区域认领数据访问"""

    def __init__(self, store: dict = _mock_store):
        self.store = store

    def get_claim(self, region: str):
        """查询区域认领者,无人认领返回 None"""
        return self.store["shipping_claims"].get(region)

    def is_claimed(self, region: str) -> bool:
        """区域是否已被认领"""
        return region in self.store["shipping_claims"]

    def set_claim(self, region: str, agent_id) -> None:
        """设置区域认领(直接覆盖,业务校验由 services 层负责)"""
        self.store["shipping_claims"][region] = agent_id

    def list_all(self) -> dict:
        """列出所有认领记录(返回副本避免外部修改)"""
        return dict(self.store["shipping_claims"])
