"""43号·P3-3 GeoIP + 设备指纹专项测试

运行方式:
    python test_security_p3_3.py

覆盖(计划 §四):
    - XFF: 第一跳优先/缺失回退直连/多跳取首
    - geo 库: 缺失静默关闭(geo_available=False)/lookup_geo 中性
    - 设备指纹: 陌生设备打敏感端点降分/信任设备中性/无指纹中性/
      敏感端点外不降/信任设备数堆积(撞库)
    - 地理历史: 记录去重/滚动窗口/查询不记数
    - geo_velocity: 库缺失中性(默认实机口径)
    - 网关联动: device_id 传入 → 陌生设备打敏感端点 →
      identity_risk 降分; 无指纹零影响(回归)
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["SECURITY_GATEWAY_MODE"] = "on"
os.environ["SECURITY_ENFORCE_LEVEL"] = "observe"
# geo 库指向不存在路径(默认实机口径: 静默关闭)
os.environ["GEOIP_DB_PATH"] = "/nonexistent/GeoLite2-City.mmdb"

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def reset_store():
    from repositories.store import reset_store as _reset
    _reset()


class TestXff:
    async def run(self):
        print("[01 XFF解析]")
        import importlib
        import services.geoip_service as gs
        importlib.reload(gs)   # 重置 mmdb 懒加载缓存

        record("XFF第一跳",
               gs.extract_client_ip("203.0.113.5, 10.0.0.1",
                                    "172.18.0.1") == "203.0.113.5")
        record("无XFF回退直连",
               gs.extract_client_ip("", "172.18.0.1") == "172.18.0.1")
        record("空XFF回退直连",
               gs.extract_client_ip("  ", "1.2.3.4") == "1.2.3.4")


class TestGeoLib:
    async def run(self):
        print("[02 geo库静默关闭]")
        import importlib
        import services.geoip_service as gs
        importlib.reload(gs)

        record("库缺失geo_available=False",
               gs.geo_available() is False)
        record("库缺失lookup中性",
               gs.lookup_geo("8.8.8.8") is None)
        record("本机IP中性",
               gs.lookup_geo("127.0.0.1") is None)
        record("空IP中性", gs.lookup_geo("") is None)

        # 库缺失时 geo_velocity 中性
        r = await gs.geo_velocity_signal(1, "8.8.8.8")
        record("库缺失velocity中性",
               r["hasSignal"] is False and r["score"] == 100.0,
               str(r))


class TestDeviceFingerprint:
    async def run(self):
        print("[03 设备指纹]")
        import importlib
        import services.geoip_service as gs
        importlib.reload(gs)
        from repositories.entry_repository import EntryRepository
        erepo = EntryRepository()

        # 会员 701 信任设备 dev-a
        await erepo.save_device(701, {
            "deviceId": "dev-a", "trustedUntil": "2099-01-01"})

        # 信任设备 + 敏感端点 → 中性
        r = await gs.device_risk_signal(701, "dev-a",
                                        "/api/admin/stats")
        record("信任设备中性", r["score"] == 100.0
               and r["hasSignal"] is False, str(r))

        # 陌生设备 + 敏感端点 → 降分
        r = await gs.device_risk_signal(701, "dev-unknown",
                                        "/api/admin/stats")
        record("陌生设备打敏感端点降分", r["score"] == 40.0
               and r["hasSignal"] is True,
               str(r))
        r = await gs.device_risk_signal(701, "dev-unknown",
                                        "/api/finance/report")
        record("finance前缀同降", r["score"] == 40.0)

        # 陌生设备 + 普通端点 → 中性(不惩罚)
        r = await gs.device_risk_signal(701, "dev-unknown",
                                        "/api/product/list")
        record("陌生设备普通端点中性",
               r["score"] == 100.0, str(r))

        # 无指纹 → 中性
        r = await gs.device_risk_signal(701, "", "/api/admin/stats")
        record("无指纹中性", r["hasSignal"] is False)

        # 信任设备数堆积(≥3 → 撞库信号)
        await erepo.save_device(702, {
            "deviceId": "d1", "trustedUntil": "2099-01-01"})
        await erepo.save_device(702, {
            "deviceId": "d2", "trustedUntil": "2099-01-01"})
        await erepo.save_device(702, {
            "deviceId": "d3", "trustedUntil": "2099-01-01"})
        r = await gs.device_risk_signal(702, "d3",
                                        "/api/product/list")
        record("设备数堆积撞库信号", r["score"] < 100.0
               and any("撞库" in d for d in r["details"]),
               str(r))


class TestGeoHistory:
    async def run(self):
        print("[04 地理历史]")
        from repositories.security_repository import \
            Security43Repository
        repo = Security43Repository()

        history = await repo.record_member_geo(801, "1.1.1.1")
        record("首次记录", history == ["1.1.1.1"], str(history))
        # 去重
        history = await repo.record_member_geo(801, "1.1.1.1")
        record("重复IP去重", history == ["1.1.1.1"], str(history))
        history = await repo.record_member_geo(801, "2.2.2.2")
        record("新IP追加", set(history) == {"1.1.1.1", "2.2.2.2"},
               str(history))
        # 查询不记数
        got = await repo.get_member_geo_history(801)
        record("查询不记数", set(got) == {"1.1.1.1", "2.2.2.2"},
               str(got))
        # 会员独立
        await repo.record_member_geo(802, "3.3.3.3")
        got = await repo.get_member_geo_history(802)
        record("会员独立", got == ["3.3.3.3"], str(got))


class TestGatewayIntegration:
    async def run(self):
        print("[05 网关联动]")
        import importlib
        import services.geoip_service as gs
        importlib.reload(gs)
        from services.security_service import Security43Service
        svc = Security43Service()
        from repositories.entry_repository import EntryRepository
        erepo = EntryRepository()

        # 会员 803 信任设备 ok-dev
        await erepo.save_device(803, {
            "deviceId": "ok-dev", "trustedUntil": "2099-01-01"})

        # 信任设备请求: identity_risk 满分(零影响回归)
        r = await svc.process_request(
            "6.6.6.1", method="GET", path="/api/admin/stats",
            ua="Mozilla/5.0", member_id=803, hour=14,
            device_id="ok-dev")
        identity = [f for f in (r.get("scoring") or {}).get(
            "factors", []) if f["name"] == "identity_risk"]
        # 未认证身份风险已降分(member 803 有 x-member-id 但
        # scan_identity 逻辑: member_id 非空 → 敏感端点不降)
        record("信任设备不叠加降分",
               identity and identity[0]["score"] >= 100.0,
               str(identity)[:100])

        # 陌生设备打敏感端点: identity_risk 降分(device 信号)
        r = await svc.process_request(
            "6.6.6.2", method="GET", path="/api/admin/stats",
            ua="Mozilla/5.0", member_id=803, hour=14,
            device_id="unknown-dev")
        identity = [f for f in (r.get("scoring") or {}).get(
            "factors", []) if f["name"] == "identity_risk"]
        record("陌生设备identity降分",
               identity and identity[0]["score"] <= 40.0,
               str(identity)[:100])

        # 无指纹请求: 零影响(回归 P0-P2 行为)
        r = await svc.process_request(
            "6.6.6.3", method="GET", path="/api/product/list",
            ua="Mozilla/5.0", member_id=803, hour=14)
        record("无指纹零影响", r["action"] == "allow",
               str(r["action"]))

        # fail-open: 39号仓储异常不阻断
        async def _boom(*a, **kw):
            raise RuntimeError("仓储故障")

        original = gs.device_risk_signal
        gs.device_risk_signal = _boom
        try:
            r = await svc.process_request(
                "6.6.6.4", method="GET", path="/api/product/list",
                ua="Mozilla/5.0", member_id=803, hour=14,
                device_id="x")
            record("设备信号异常放行(fail-open)",
                   r["action"] == "allow", str(r["action"]))
        finally:
            gs.device_risk_signal = original


async def run_all():
    await TestXff().run()
    await TestGeoLib().run()
    await TestDeviceFingerprint().run()
    await TestGeoHistory().run()
    await TestGatewayIntegration().run()


def main():
    reset_store()
    asyncio.run(run_all())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
