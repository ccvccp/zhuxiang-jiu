"""cooperation PII 治理测试(2026-10-18 推广, 对齐 86号三件套范式)

覆盖: core.pii 公共件 / 出口脱敏(列表/详情/公开 partners) /
详情端点鉴权漏洞修复 / 导出权+审计留痕 / 删除权 / TTL 匿名化边界
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from core.pii import mask_contact, mask_email, mask_phone


def test_core_pii_utils():
    assert mask_phone("13812341234") == "138****1234"
    assert mask_email("zhang@example.com") == "z***@example.com"
    rec = {"contactPhone": "13900001111",
           "contactEmail": "li@wine.cn", "name": "竹香酒业"}
    out = mask_contact(dict(rec))
    assert out["contactPhone"] == "139****1111"
    assert "****" in out["contactEmail"] or "***" in out["contactEmail"]
    assert out["name"] == "竹香酒业"          # 非联系人字段不动


@pytest.fixture()
def client():
    from main import app
    return TestClient(app)


async def _seed_app(app_id: int, *, status: str = "rejected",
                    age_days: int = 0) -> dict:
    from datetime import datetime, timedelta
    from repositories.cooperation_repository import (
        CooperationRepository)
    repo = CooperationRepository()
    app = {"id": app_id, "applicationNo": f"CA{app_id}",
           "memberId": 1, "partnerId": 1, "partnerName": "竹香酒业",
           "partnerNo": "PT1", "partnerType": "enterprise",
           "type": "supply", "businessScope": "酒类供应",
           "estimatedAmount": 10000,
           "contactName": "张三", "contactPhone": "13811112222",
           "contactEmail": "zhang@wine.cn",
           "qualificationFiles": [], "deliveryDate": "",
           "status": status, "reviewScore": 0, "reviewRemark": "",
           "contractId": None,
           "createdAt": (datetime.utcnow()
                         - timedelta(days=age_days)).isoformat(),
           "updatedAt": datetime.utcnow().isoformat()}
    await repo.save_application(app)
    return app


def test_export_gate_and_mask(client):
    """详情端点漏洞修复(原公开明文→403) + 列表/详情/导出三口径"""
    asyncio.run(_seed_app(99001, status="pending"))

    # 漏洞修复: 详情无 X-Role → 403(此前公开返回明文)
    r = client.get("/api/cooperation/applications/99001")
    assert r.status_code == 403

    admin = {"X-Role": "admin"}
    # admin 列表: 联系人出口脱敏
    rows = client.get("/api/cooperation/applications",
                      headers=admin).json()["data"]
    row = next(x for x in rows if x["id"] == 99001)
    assert row["contactPhone"] == "138****2222"
    assert "***" in row["contactEmail"]
    # admin 详情: 同样脱敏
    d = client.get("/api/cooperation/applications/99001",
                   headers=admin).json()["data"]
    assert d["contactPhone"] == "138****2222"
    # 导出: 完整明文(数据主体副本语义) + 审计留痕
    e = client.get("/api/cooperation/applications/99001/export",
                   headers=admin).json()["data"]
    assert e["contactPhone"] == "13811112222"

    from repositories.cooperation_repository import (
        CooperationRepository)
    exports = CooperationRepository()._ensure_store() or None
    store = CooperationRepository().store
    logs = store.get("cooperation_exports", [])
    assert logs and "138****2222" in logs[0]["phoneMasked"]

    # 删除权: 删后 404
    assert client.delete("/api/cooperation/applications/99001",
                         headers=admin).status_code == 200
    assert client.delete("/api/cooperation/applications/99001",
                         headers=admin).status_code == 404


def test_partners_public_masked(client):
    """公开 partners 端点: 联系人电话/邮箱脱敏(公司名保留)"""
    asyncio.run(_seed_partner())

    r = client.get("/api/cooperation/partners")
    assert r.status_code == 200
    rows = r.json()["data"]
    assert rows, "应有种子合作方"
    for p in rows:
        if p.get("contactPhone"):
            assert "****" in p["contactPhone"], p["contactPhone"]


async def _seed_partner():
    from datetime import datetime
    from repositories.cooperation_repository import (
        CooperationRepository)
    repo = CooperationRepository()
    pid = await repo.next_partner_id()
    await repo.save_partner({
        "id": pid, "partnerNo": f"PT{pid}", "name": "PII测试酒业",
        "type": "enterprise", "contactName": "李四",
        "contactPhone": "13733334444", "contactEmail": "l4@wine.cn",
        "qualStatus": "pending", "level": "bronze",
        "status": "pending", "totalAmount": 0, "contractCount": 0,
        "createdAt": datetime.utcnow().isoformat(),
        "updatedAt": datetime.utcnow().isoformat()})


def test_ttl_cooperation_boundary():
    """TTL: 超期未签约匿名化; approved(合同关系)不动"""
    asyncio.run(_ttl_body())


async def _ttl_body():
    from repositories.cooperation_repository import (
        APP_STATUS_APPROVED, CooperationRepository)
    repo = CooperationRepository()
    await _seed_app(99002, status="rejected", age_days=91)   # 冷数据
    await _seed_app(99003, status=APP_STATUS_APPROVED,
                    age_days=91)                            # 合同关系
    await _seed_app(99004, status="pending", age_days=0)    # 未到期
    report = await repo.expire_applications(max_age_days=90)
    assert report["expired"] >= 1
    apps = {a["id"]: a for a in await repo.list_applications(
        limit=500)}
    assert apps[99002]["contactPhone"] == ""
    assert apps[99002]["anonymizedAt"]
    assert apps[99003]["contactPhone"] == "13811112222"      # 保留
    assert apps[99004]["contactPhone"] == "13811112222"      # 未到期
    # 幂等
    again = await repo.expire_applications(max_age_days=90)
    assert again["expired"] == 0


def test_purge_cooperation_boundary():
    """物理压实: 匿名化满365天删除; 未满/未匿名化不动; 幂等"""
    asyncio.run(_purge_body())


async def _purge_body():
    from datetime import datetime, timedelta
    from repositories.cooperation_repository import (
        CooperationRepository)
    repo = CooperationRepository()
    # 已匿名化且超 365 天 → 删
    old = await _seed_app(99005, status="rejected", age_days=500)
    old["anonymizedAt"] = (datetime.utcnow()
                           - timedelta(days=400)).isoformat()
    await repo.save_application(old)
    # 已匿名化但未满 365 天 → 留
    fresh = await _seed_app(99006, status="rejected", age_days=91)
    fresh["anonymizedAt"] = datetime.utcnow().isoformat()
    await repo.save_application(fresh)
    # 未匿名化(approved) → 永不动
    await _seed_app(99007, status="approved", age_days=500)

    report = await repo.purge_applications(max_anon_age_days=365)
    assert report["purged"] >= 1
    ids = {a["id"] for a in await repo.list_applications(limit=500)}
    assert 99005 not in ids                 # 已压实
    assert 99006 in ids                     # 匿名化未满期
    assert 99007 in ids                     # 未匿名化豁免
    # 幂等
    again = await repo.purge_applications(max_anon_age_days=365)
    assert again["purged"] == 0
    # 清理
    for i in (99006, 99007):
        await repo.delete_application(i)
