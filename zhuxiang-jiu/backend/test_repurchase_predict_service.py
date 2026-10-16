# -*- coding: utf-8 -*-
"""无感复购预测·最小闭环单测(repurchase_predict_service + 路由冒烟)

覆盖: 周期中位数 / <2 笔不可预测 / qFactor 三档+小样本跳过 /
窗口命中与逾期判定 / 幂等(同日重复扫描) / observe 拒派发 /
enforce 派发成功(打桩 MessageService 断言参数)+状态流转 /
fail-soft(单会员订单异常不中断) / 模式闸 / 路由冒烟(X-Role)。

运行:
    python -m pytest test_repurchase_predict_service.py -q
"""
import asyncio
import os
from datetime import UTC, datetime, timedelta

# 环境自举(先于 services 导入, 对齐 test_zhike 形态; 强制内存模式)
os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")

import pytest  # noqa: E402

from services.repurchase_predict_service import (  # noqa: E402
    RepurchasePredictService, repurchase_push_mode, SUGGESTION_TABLE)
from repositories.message_repository import (  # noqa: E402
    CATEGORY_MEMBER, CHANNEL_INMAIL)


# ============================================================
# 打桩(简单 dict 内存实现, 形态对齐 test_growth80_pricing)
# ============================================================

def _days_ago(n: float) -> str:
    return (datetime.now(UTC) - timedelta(days=n)).isoformat()


def _member(mid: int, **extra) -> dict:
    data = {"id": mid, "nickname": f"会员{mid}", "status": 1,
            "level": 2, "growth_value": 0, "points": 100,
            "created_at": _days_ago(400), "last_login_at": _days_ago(1)}
    data.update(extra)
    return data


def _order(paid_days_ago: float, status: str = "PAID") -> dict:
    """订单 dict(字段名对齐织物口径: payment.paidAt / status)"""
    return {"id": int(paid_days_ago * 1000), "status": status,
            "payment": {"paidAt": _days_ago(paid_days_ago)},
            "priceDetail": {"actualAmount": 128.0}}


class _StubFabric:
    """织物打桩: members() + member_orders()(乱序返回验证排序)"""

    def __init__(self, members: list, orders_by_member: dict):
        self._members = members
        self._orders = orders_by_member

    async def members(self, limit: int = 500) -> list:
        return list(self._members)

    async def member_orders(self, member_id) -> list:
        return list(reversed(self._orders.get(member_id, [])))


class _BrokenFabric(_StubFabric):
    """fail-soft 打桩: 指定会员订单读取抛错"""

    def __init__(self, members, orders_by_member, broken_id):
        super().__init__(members, orders_by_member)
        self._broken_id = broken_id

    async def member_orders(self, member_id) -> list:
        if member_id == self._broken_id:
            raise RuntimeError("订单数据异常")
        return await super().member_orders(member_id)


class _StubStore:
    """留痕打桩: next_id / save / get / list(纯 dict)"""

    def __init__(self):
        self.tables: dict = {}
        self._seq: dict = {}

    async def next_id(self, entity: str) -> int:
        self._seq[entity] = self._seq.get(entity, 0) + 1
        return self._seq[entity]

    async def save(self, table: str, record_id, record: dict) -> None:
        self.tables.setdefault(table, {})[record_id] = record

    async def get(self, table: str, record_id):
        return self.tables.get(table, {}).get(record_id)

    async def list(self, table: str) -> list:
        return list(self.tables.get(table, {}).values())


class _StubMessageService:
    """消息服务打桩: 只记录 send_message 调用参数"""

    def __init__(self):
        self.calls: list[dict] = []

    async def send_message(self, user_id, channel, title, content,
                           category=None, **kwargs) -> dict:
        self.calls.append({"user_id": user_id, "channel": channel,
                           "title": title, "content": content,
                           "category": category, "kwargs": kwargs})
        return {"id": 99001, "userId": user_id, "channel": channel,
                "category": category, "title": title, "content": content}


def _svc(members=None, orders=None, broken_id=None,
         message_service=None) -> RepurchasePredictService:
    fabric = (_BrokenFabric(members, orders, broken_id)
              if broken_id is not None
              else _StubFabric(members or [], orders or {}))
    return RepurchasePredictService(
        fabric=fabric, store=_StubStore(),
        message_service=message_service)


# ---------- T0 模式闸 ----------

def test_t0_mode_gate(monkeypatch):
    """默认 observe / 三态 / 非法值回落(对齐 multiplier_mode 范式)"""
    monkeypatch.delenv("REPURCHASE_PUSH_MODE", raising=False)
    assert repurchase_push_mode() == "observe"
    for v in ("off", "observe", "enforce"):
        monkeypatch.setenv("REPURCHASE_PUSH_MODE", v)
        assert repurchase_push_mode() == v
    monkeypatch.setenv("REPURCHASE_PUSH_MODE", "garbage")
    assert repurchase_push_mode() == "observe"


# ---------- T1 周期中位数 ----------

def test_t1_cycle_median():
    """3 个间隔 10/20/30 → 中位数 20; 乱序订单按支付时间排序"""

    async def run():
        svc = _svc()
        pred = await svc.predict_member(_member(1), [
            _order(30), _order(60), _order(0), _order(50)])
        assert pred["predictable"] is True
        assert pred["orderCount"] == 4
        assert pred["cycleDays"] == 20.0        # median(10,20,30)=20
        assert pred["dueInDays"] == pytest.approx(20.0, abs=0.2)

    asyncio.run(run())


# ---------- T2 <2 笔不可预测 ----------

def test_t2_unpredictable():
    """0/1 笔, 以及非有效状态订单不计入 → predictable=False"""

    async def run():
        svc = _svc()
        r0 = await svc.predict_member(_member(1), [])
        assert r0["predictable"] is False and r0["orderCount"] == 0
        r1 = await svc.predict_member(_member(1), [_order(10)])
        assert r1["predictable"] is False and r1["orderCount"] == 1
        # 待支付/退款单不算消费
        r2 = await svc.predict_member(_member(1), [
            _order(30, status="PENDING"), _order(10, status="REFUNDED")])
        assert r2["predictable"] is False

    asyncio.run(run())


# ---------- T3 qFactor 三档 + 小样本跳过 ----------

def test_t3_qfactor_tiers():
    """CV≤0.3→1.2 / ≤0.6→1.0 / >0.6→0.8 / 间隔样本<4→1.0"""

    async def run():
        svc = _svc()
        # 规律型: 间隔全 10 → CV=0 → 1.2
        reg = await svc.predict_member(_member(1), [
            _order(d) for d in (50, 40, 30, 20, 10, 0)])
        assert reg["qFactor"] == 1.2 and reg["cv"] == 0.0
        # 常规型: 间隔 10/20/10/20 → CV=1/3 → 1.0
        nor = await svc.predict_member(_member(1), [
            _order(d) for d in (60, 50, 30, 20, 0)])
        assert nor["qFactor"] == 1.0 and nor["cv"] == pytest.approx(
            1 / 3, abs=0.01)
        # 波动型: 间隔 5/30/5/30 → CV≈0.714 → 0.8
        vol = await svc.predict_member(_member(1), [
            _order(d) for d in (70, 65, 35, 30, 0)])
        assert vol["qFactor"] == 0.8 and vol["cv"] == pytest.approx(
            5 / 7, abs=0.01)
        # 小样本: 3 笔(2 个间隔) → 跳过 1.0, cv=None
        small = await svc.predict_member(_member(1), [
            _order(d) for d in (20, 10, 0)])
        assert small["qFactor"] == 1.0 and small["cv"] is None

    asyncio.run(run())


# ---------- T4 窗口命中 / 逾期判定 ----------

def test_t4_window_and_overdue():
    """0≤due≤window→upcoming / due<-7→overdue / 其余不产建议"""

    async def run():
        members = [
            _member(1),  # due≈2 → upcoming
            _member(2),  # due≈-15 → overdue
            _member(3),  # due≈-3 → 逾期未满 7 天, 不产
            _member(4),  # due≈10 → 窗口外, 不产
            _member(5),  # 仅 1 笔 → 不可预测
        ]
        orders = {
            1: [_order(38), _order(18)],    # 周期 20, 末单 18 天前 → due≈2
            2: [_order(35), _order(25)],    # 周期 10, 末单 25 天前 → due≈-15
            3: [_order(23), _order(13)],    # 周期 10, 末单 13 天前 → due≈-3
            4: [_order(30), _order(10)],    # 周期 20, 末单 10 天前 → due≈10
            5: [_order(5)],
        }
        svc = _svc(members, orders)
        result = await svc.scan_due(window_days=3)
        assert result["scanned"] == 5
        assert result["predictable"] == 4
        assert result["upcoming"] == 1
        assert result["overdue"] == 1
        assert result["skipped"] == 1          # 仅会员 5 不可预测
        assert result["mode"] == "observe"

        rows = await svc.suggestions()
        assert len(rows) == 2
        by_tier = {r["tier"]: r for r in rows}
        up, od = by_tier["upcoming"], by_tier["overdue"]
        assert up["memberId"] == 1 and up["status"] == "pending"
        assert "复购提醒" in up["suggestion"] and "竹叶余额" in up["suggestion"]
        assert od["memberId"] == 2 and od["status"] == "pending"
        assert "关怀" in od["suggestion"] and "权益" in od["suggestion"]
        assert od["dueInDays"] < -7

    asyncio.run(run())


# ---------- T5 幂等(同日重复扫描不重复产) ----------

def test_t5_idempotent_same_day():
    """同会员同 tier 当日已有 pending → 跳过, 不重复产"""

    async def run():
        members = [_member(1), _member(2)]
        orders = {
            1: [_order(38), _order(18)],   # upcoming
            2: [_order(35), _order(25)],   # overdue
        }
        svc = _svc(members, orders)
        r1 = await svc.scan_due(window_days=3)
        assert r1["upcoming"] == 1 and r1["overdue"] == 1
        r2 = await svc.scan_due(window_days=3)
        assert r2["upcoming"] == 0 and r2["overdue"] == 0
        assert r2["skipped"] == 2           # 两会员均幂等跳过
        rows = await svc.suggestions()
        assert len(rows) == 2               # 留痕仍只有 2 条 pending
        assert all(r["status"] == "pending" for r in rows)

    asyncio.run(run())


# ---------- T6 observe 模式 dispatch 被拒 ----------

def test_t6_dispatch_rejected_in_observe(monkeypatch):
    """observe 下 dispatch 拒绝并提示当前模式; off 同样拒绝"""

    async def run():
        monkeypatch.setenv("REPURCHASE_PUSH_MODE", "observe")
        msg = _StubMessageService()
        svc = _svc([_member(1)], {1: [_order(38), _order(18)]},
                   message_service=msg)
        await svc.scan_due()
        sid = (await svc.suggestions())[0]["suggestionId"]
        with pytest.raises(ValueError) as exc:
            await svc.dispatch(sid, "管理员甲")
        assert "observe" in str(exc.value)
        assert msg.calls == []               # 绝未触达

        monkeypatch.setenv("REPURCHASE_PUSH_MODE", "off")
        with pytest.raises(ValueError):
            await svc.dispatch(sid, "管理员甲")

    asyncio.run(run())


# ---------- T7 enforce 派发成功 + 状态流转 ----------

def test_t7_dispatch_enforce_success(monkeypatch):
    """enforce 下 dispatch 调 message(inail/member 类)且参数正确;
    成功后 status='sent'+sentAt 留痕; 非 pending/不存在均拒绝"""

    async def run():
        monkeypatch.setenv("REPURCHASE_PUSH_MODE", "enforce")
        msg = _StubMessageService()
        members = [_member(7)]
        orders = {7: [_order(38), _order(18)]}   # upcoming
        svc = _svc(members, orders, message_service=msg)
        await svc.scan_due()
        row = (await svc.suggestions())[0]
        sid = row["suggestionId"]

        result = await svc.dispatch(sid, "管理员甲")
        # 消息参数断言: 站内信 + 会员类营销分类 + 会员 ID
        assert len(msg.calls) == 1
        call = msg.calls[0]
        assert call["user_id"] == 7
        assert call["channel"] == CHANNEL_INMAIL == "inmail"
        assert call["category"] == CATEGORY_MEMBER == "member"
        assert "复购提醒" in call["title"]
        assert "消耗周期" in call["content"]
        # 状态流转留痕
        assert result["status"] == "sent" and result["sentBy"] == "管理员甲"
        stored = await svc.store.get(SUGGESTION_TABLE, sid)
        assert stored["status"] == "sent" and stored["sentAt"]
        assert stored["messageId"] == 99001
        # 已 sent 再派发 → 拒绝(非 pending)
        with pytest.raises(ValueError):
            await svc.dispatch(sid, "管理员甲")
        # 不存在 → KeyError
        with pytest.raises(KeyError):
            await svc.dispatch(99999, "管理员甲")

    asyncio.run(run())


# ---------- T8 fail-soft(单会员订单异常不中断整扫) ----------

def test_t8_fail_soft():
    """会员 1 订单读取抛错 → 跳过并告警, 会员 2 正常产建议"""

    async def run():
        members = [_member(1), _member(2)]
        orders = {
            1: [_order(38), _order(18)],
            2: [_order(35), _order(25)],    # overdue
        }
        svc = _svc(members, orders, broken_id=1)
        result = await svc.scan_due(window_days=3)
        assert result["scanned"] == 2
        assert result["overdue"] == 1        # 会员 2 正常产出
        assert result["skipped"] == 1        # 会员 1 异常跳过, 未中断
        rows = await svc.suggestions()
        assert [r["memberId"] for r in rows] == [2]

    asyncio.run(run())


# ---------- T9 off 模式不扫描 ----------

def test_t9_off_no_scan(monkeypatch):
    """off: 不扫描不产建议(性能兜底)"""

    async def run():
        monkeypatch.setenv("REPURCHASE_PUSH_MODE", "off")
        svc = _svc([_member(1)], {1: [_order(38), _order(18)]})
        result = await svc.scan_due()
        assert result["mode"] == "off" and result["scanned"] == 0
        assert await svc.suggestions() == []

    asyncio.run(run())


# ---------- R1/R2 路由冒烟(裸 FastAPI 挂 zk 路由, 打桩服务) ----------

def _load_zk_routes_module():
    """直接从文件加载 zk_routes(绕过 routes/__init__.py——其导入的
    community_routes.py 为存量语法错误半成品, 与本模块无关)"""
    import importlib.util
    import sys
    name = "zk_routes_direct_for_test"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "routes", "zk_routes.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


def _route_client_with(monkeypatch, svc) -> "TestClient":
    """裸 FastAPI 挂载 zk 路由(不经 main 中间件, X-Role 直通)"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    zr = _load_zk_routes_module()
    monkeypatch.setattr(zr, "_repurchase", svc)
    app = FastAPI()
    zr.register_zk_routes(app)
    return TestClient(app)


def test_r1_route_scan_smoke(monkeypatch):
    """scan/suggestions 冒烟: admin 200 + observe 模式; 无 X-Role 403"""

    async def run():
        monkeypatch.setenv("REPURCHASE_PUSH_MODE", "observe")
        svc = _svc([_member(1)], {1: [_order(38), _order(18)]})
        await svc.scan_due()
        return svc

    client = _route_client_with(monkeypatch, asyncio.run(run()))
    r = client.get("/api/member-ai/repurchase/scan",
                   headers={"X-Role": "admin"},
                   params={"window_days": 3})
    assert r.status_code == 200
    assert r.json()["data"]["mode"] == "observe"
    r2 = client.get("/api/member-ai/repurchase/suggestions",
                    headers={"X-Role": "admin"})
    assert r2.status_code == 200 and r2.json()["count"] == 1
    assert client.get("/api/member-ai/repurchase/scan"
                      ).status_code == 403   # 无角色 → 403


def test_r2_route_dispatch_smoke(monkeypatch):
    """observe 下 dispatch 路由 → 409(模式闸); 缺 operator → 422"""

    async def run():
        monkeypatch.setenv("REPURCHASE_PUSH_MODE", "observe")
        svc = _svc([_member(2)], {2: [_order(35), _order(25)]})
        await svc.scan_due()
        return svc

    client = _route_client_with(monkeypatch, asyncio.run(run()))
    r = client.post("/api/member-ai/repurchase/dispatch",
                    headers={"X-Role": "admin"},
                    json={"suggestionId": 1, "operator": "管理员甲"})
    assert r.status_code == 409 and "observe" in r.json()["detail"]
    r2 = client.post("/api/member-ai/repurchase/dispatch",
                     headers={"X-Role": "admin"}, json={"suggestionId": 1})
    assert r2.status_code == 422             # 缺 operator → 422


# ---------- T10 stats 聚合正确性(R1) ----------

def test_t10_stats_aggregation(monkeypatch):
    """stats: byTier/byQFactor/last7dDaily/dispatchRate 聚合正确
    (cv=None → byQFactor.skipped; 7 天窗口外不计入日分桶)"""

    async def run():
        monkeypatch.setenv("REPURCHASE_PUSH_MODE", "observe")
        svc = _svc()
        now = datetime.now(UTC)
        # 6 条留痕: (tier, qFactor, cv, status, createdAt 距今天数)
        records = [
            ("upcoming", 1.2, 0.05, "pending", 0),
            ("upcoming", 1.2, 0.10, "sent", 1),
            ("overdue", 1.0, 0.40, "pending", 2),
            ("overdue", 1.0, None, "sent", 3),     # 小样本 → skipped
            ("overdue", 0.8, 0.90, "pending", 6),
            ("overdue", 0.8, 0.95, "pending", 8),  # 7 天窗口外
        ]
        for i, (tier, qf, cv, status, d_ago) in enumerate(records, 1):
            rec = {"suggestionId": i, "memberId": i, "tier": tier,
                   "qFactor": qf, "cv": cv, "status": status,
                   "createdAt": (now - timedelta(days=d_ago)).isoformat()}
            await svc.store.save(SUGGESTION_TABLE, i, rec)

        s = await svc.stats()
        assert s["total"] == 6
        assert s["byTier"] == {"upcoming": 2, "overdue": 4}
        # skipped=1(小样本 cv=None); 1.2×2 / 1.0×1 / 0.8×2
        assert s["byQFactor"] == {"1.2": 2, "1.0": 1, "0.8": 2,
                                  "skipped": 1}
        # dispatchRate = sent/(sent+pending) = 2/6
        assert s["dispatchRate"] == pytest.approx(2 / 6, abs=0.01)
        assert s["mode"] == "observe"
        # last7dDaily: 恒 7 天旧→新, 今日在末位; 8 天前不计入 → 共 5 条
        daily = s["last7dDaily"]
        assert len(daily) == 7
        assert daily[-1]["date"] == now.strftime("%Y-%m-%d")
        assert daily[-1]["count"] == 1
        assert daily[0]["count"] == 1            # 6 天前那条
        assert sum(d["count"] for d in daily) == 5
        # 与 suggestions 列表人工抽核对齐: 分桶总和 = total
        assert (sum(s["byTier"].values())
                == sum(s["byQFactor"].values()) == s["total"])

    asyncio.run(run())


# ---------- T11 stats 空留痕全零(R1) ----------

def test_t11_stats_empty(monkeypatch):
    """空留痕: 全零结构 + dispatchRate=0.0(除零兜底) + mode 回显"""

    async def run():
        monkeypatch.setenv("REPURCHASE_PUSH_MODE", "observe")
        svc = _svc()
        s = await svc.stats()
        assert s["total"] == 0
        assert s["byTier"] == {"upcoming": 0, "overdue": 0}
        assert s["byQFactor"] == {"1.2": 0, "1.0": 0, "0.8": 0,
                                  "skipped": 0}
        assert s["dispatchRate"] == 0.0
        assert len(s["last7dDaily"]) == 7
        assert all(d["count"] == 0 for d in s["last7dDaily"])
        assert s["mode"] == "observe"

    asyncio.run(run())


# ---------- T12 调度 auto off 零副作用(R2) ----------

def test_t12_scheduler_auto_off_noop(monkeypatch):
    """REPURCHASE_SCAN_AUTO 默认 off → start_scan_loop 零副作用
    (打桩 RepurchasePredictService, 断言 scan_due 从未被调)"""
    import services.repurchase_predict_service as rps
    calls = []

    class _Recorder:
        def __init__(self, *args, **kwargs):
            calls.append("instantiated")

        async def scan_due(self, window_days=3):
            calls.append(("scan_due", window_days))
            return {"mode": "observe", "scanned": 0}

    monkeypatch.setattr(rps, "RepurchasePredictService", _Recorder)
    monkeypatch.delenv("REPURCHASE_SCAN_AUTO", raising=False)
    from services import repurchase_scan_scheduler as rss
    rss.start_scan_loop()                 # 同步调用后直接返回
    assert calls == []                    # 未实例化未扫描, 零副作用


# ---------- T13 调度 auto on 启动即扫一轮(R2) ----------

def test_t13_scheduler_auto_on_scans(monkeypatch):
    """auto on + observe 模式 → 启动即跑一轮 scan_due(window_days=3),
    之后进入 sleep 等下一轮(interval 到期由 _interval 保证)"""

    async def run():
        import services.repurchase_predict_service as rps
        calls = []

        class _Recorder:
            def __init__(self, *args, **kwargs):
                pass

            async def scan_due(self, window_days=3):
                calls.append(window_days)
                return {"mode": "observe", "scanned": 1, "predictable": 1,
                        "upcoming": 1, "overdue": 0, "skipped": 0}

        monkeypatch.setattr(rps, "RepurchasePredictService", _Recorder)
        monkeypatch.setenv("REPURCHASE_SCAN_AUTO", "on")
        monkeypatch.setenv("REPURCHASE_PUSH_MODE", "observe")
        monkeypatch.setenv("REPURCHASE_SCAN_INTERVAL_MIN", "60")
        from services import repurchase_scan_scheduler as rss
        rss.start_scan_loop()             # 事件循环内 → create_task
        await asyncio.sleep(0.1)          # 让首轮 run_once 执行完
        assert calls == [3]               # 启动立即扫一轮, 窗口 3 天

    asyncio.run(run())
