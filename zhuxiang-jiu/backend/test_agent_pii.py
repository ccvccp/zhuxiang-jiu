"""agent PII 治理测试(2026-10-18 第二批推广, 对齐 cooperation 三件套范式)

覆盖: 出口脱敏(admin 申请列表 / 公开档案列表与详情) /
导出权+审计留痕 / 删除权 / TTL 匿名化边界(approved 不动)
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from core.pii import mask_contact


def test_mask_snake_case_contact():
    """core.pii 公共件默认键集覆盖 agent 的 snake_case 字段"""
    rec = {"company_name": "竹香酒业", "contact_name": "张三",
           "contact_phone": "13812341234", "region": "华东"}
    out = mask_contact(dict(rec))
    assert out["contact_phone"] == "138****1234"
    assert out["contact_name"] == "张三"
    assert out["company_name"] == "竹香酒业"


@pytest.fixture()
def client():
    from main import app
    return TestClient(app)


async def _seed_apply(apply_id: int, *, status: str = "pending",
                      age_days: int = 0) -> dict:
    from datetime import datetime, timedelta
    from repositories.agent_repository import AgentRepository
    repo = AgentRepository()
    app = {"applyId": apply_id, "company_name": "PII测试酒业",
           "contact_name": "李四", "contact_phone": "13811112222",
           "region": "华东", "apply_level": "C", "status": status,
           "audit_remark": "",
           "created_at": (datetime.utcnow()
                          - timedelta(days=age_days)).isoformat()}
    await repo.save_apply(app)
    return app


async def _seed_agent(agent_id: int):
    from datetime import datetime
    from repositories.agent_repository import AgentRepository
    repo = AgentRepository()
    repo.store.setdefault("agents", {})
    await repo.save(agent_id, {
        "id": agent_id, "name": "公开脱敏测试", "level": "C",
        "wallet": 0.0, "status": "active", "contact_name": "王五",
        "contact_phone": "13955553333", "region": "华南", "address": "",
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
        "total_sales": 0.0, "total_purchases": 0.0})


def test_admin_export_delete_and_mask(client):
    """申请列表脱敏 + 导出完整副本审计 + 删除权"""
    asyncio.run(_seed_apply(99011, status="pending"))

    # 无 admin → 403
    assert client.get("/api/agent/applications").status_code == 403

    admin = {"X-Role": "admin"}
    # admin 列表: 出口脱敏
    rows = client.get("/api/agent/applications",
                      headers=admin).json()["applications"]
    row = next(x for x in rows if x["applyId"] == 99011)
    assert row["contact_phone"] == "138****2222"

    # 导出: 完整明文(数据主体副本语义) + 审计留痕
    e = client.get("/api/agent/applications/99011/export",
                   headers=admin).json()["data"]
    assert e["contact_phone"] == "13811112222"
    from repositories.agent_repository import AgentRepository
    logs = AgentRepository().store.get("agent_exports", [])
    assert logs and "138****2222" in logs[0]["phoneMasked"]

    # 删除权: 删后 404
    assert client.delete("/api/agent/applications/99011",
                         headers=admin).status_code == 200
    assert client.delete("/api/agent/applications/99011",
                         headers=admin).status_code == 404


def test_public_agent_endpoints_masked(client):
    """公开档案端点(list / {id}): 联系人电话脱敏"""
    asyncio.run(_seed_agent(99012))

    rows = client.get("/api/agent/list").json()["agents"]
    row = next(x for x in rows if x["id"] == 99012)
    assert row["contact_phone"] == "139****3333"

    d = client.get("/api/agent/99012").json()["agent"]
    assert d["contact_phone"] == "139****3333"


def test_ttl_agent_boundary():
    """TTL: 超期未签约匿名化; approved(已转签约档案)不动"""
    asyncio.run(_ttl_body())


async def _ttl_body():
    from repositories.agent_repository import AgentRepository
    repo = AgentRepository()
    await _seed_apply(99013, status="rejected", age_days=91)   # 冷数据
    await _seed_apply(99014, status="approved", age_days=91)   # 签约来源
    await _seed_apply(99015, status="pending", age_days=0)     # 未到期
    report = await repo.expire_applies(max_age_days=90)
    assert report["expired"] >= 1
    apps = {a["applyId"]: a for a in await repo.list_applies()}
    assert apps[99013]["contact_phone"] == ""
    assert apps[99013]["anonymizedAt"]
    assert apps[99014]["contact_phone"] == "13811112222"      # 保留
    assert apps[99015]["contact_phone"] == "13811112222"      # 未到期
    # 幂等
    again = await repo.expire_applies(max_age_days=90)
    assert again["expired"] == 0
    # 清理测试数据
    for i in (99013, 99014, 99015):
        await repo.delete_apply(i)
