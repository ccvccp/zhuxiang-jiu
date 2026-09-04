"""45号·信值模块 P1 专项测试(AI 雷达三通道)

运行方式:
    python test_trust_value_p1.py

覆盖(计划 §四):
    - 验真三道关: 多模态(摆拍/刷单/过短/缺编号)/跨源(孤证
      拒绝/双源过/权威源单源过)/意图(表演式向善折减/真善过)
    - 管线串联: 最低置信度取值/0.7 阈值/unverified 不入分
    - 公开域雷达: mock 确定性(同档案幂等)/发现集入分/
      去标识化(事件不含证件明文)
    - 授权探针: 合法 provider 读数入分/非法 provider 拒绝/
      授权留痕(probe_auth 事件)/留痕列表
    - 自愿存证: 孤证拒绝(不入分留痕)/权威源过/因果净贡献
      (反事实基线剔除)/表演式向善拦截/参数校验
    - 因果效应: 自然增长剔除/容差口径/零贡献截断
    - HTTP 层: 五端点鉴权与结构
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ.pop("TRUST_RADAR_MODE", None)

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


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


async def mk_profile(svc, name, id_number, role="person"):
    return await svc.create_role(role, name, id_number)


class TestMultimodal:
    async def run(self):
        print("[01 多模态真伪鉴别]")
        from services.trust_radar_service import multimodal_check

        s, n = multimodal_check("media", "权威媒体报道 2026-08-15 编号A123")
        record("正常证据过", s == 1.0, f"{s} {n}")

        s, n = multimodal_check("media", "短")
        record("过短证据0", s == 0.0, f"{s} {n}")

        s, n = multimodal_check("media", "该照片为剧组摆拍道具场景 2026-08")
        record("摆拍特征0.2", s == 0.2 and "摆拍" in n, f"{s} {n}")

        s, n = multimodal_check("media", "某活动代打卡刷单记录一份 2026-08")
        record("刷单特征0.2", s == 0.2 and "刷量" in n, f"{s} {n}")

        s, n = multimodal_check("media", "见义勇为事迹描述无编号信息")
        record("缺可核验要素0.5", s == 0.5 and "可核验" in n,
               f"{s} {n}")


class TestCrossSource:
    async def run(self):
        print("[02 跨源交叉验证]")
        from services.trust_radar_service import cross_source_check

        ok, s, n = cross_source_check([])
        record("无源拒绝", ok is False and s == 0.0, f"{s} {n}")

        ok, s, n = cross_source_check(["media"])
        record("单源孤证拒绝", ok is False and s == 0.3
               and "孤证" in n, f"{s} {n}")

        ok, s, n = cross_source_check(["media", "self_deposit"])
        record("双独立源过", ok is True and s == 0.9, f"{s} {n}")

        ok, s, n = cross_source_check(["court"])
        record("权威源单源过", ok is True and s == 1.0
               and "权威" in n, f"{s} {n}")

        ok, s, n = cross_source_check(["gov_penalty"])
        record("处罚公示权威过", ok is True, f"{s} {n}")


class TestIntent:
    async def run(self):
        print("[03 意图推理]")
        from services.trust_radar_service import intent_check

        s, n = intent_check("社区志愿服务 40 小时(红十字编号12345)")
        record("真善过", s == 0.95, f"{s} {n}")

        s, n = intent_check("慈善晚宴宣传稿(品牌露出)")
        record("表演式向善0.3", s == 0.3 and "表演" in n, f"{s} {n}")

        s, n = intent_check("公益活动蹭热度营销")
        record("蹭热度0.3", s == 0.3, f"{s} {n}")

        s, n = intent_check("")
        record("空描述保守0.6", s == 0.6, f"{s} {n}")


class TestPipeline:
    async def run(self):
        print("[04 验真管线串联]")
        from services.trust_radar_service import verify_pipeline

        v = verify_pipeline("media",
                            "权威媒体报道 2026-08-15 编号A123",
                            ["media", "self_deposit"], "正面报道")
        record("全过verified", v["verified"] is True
               and v["confidence"] == 0.9,
               str(v["confidence"]))

        v = verify_pipeline("media", "见义勇为事迹描述无编号",
                            ["media"], "")
        record("多关夹逼拒绝", v["verified"] is False,
               str(v["confidence"]))
        record("分关明细3条", len(v["checks"]) == 3,
               str(len(v.get("checks", []))))

        v = verify_pipeline("media", "剧组摆拍道具记录 2026-08-01",
                            ["court"], "见义勇为")
        record("摆拍权威源仍拒", v["verified"] is False,
               str(v["confidence"]))
        record("指纹生成", len(v["fingerprint"]) == 16,
               str(v["fingerprint"]))


class TestRadarScan:
    async def run(self):
        print("[05 公开域雷达扫描]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_radar_service import (
            TrustRadarService, radar_mode,
        )
        ps = TrustProfileService()
        radar = TrustRadarService()

        record("默认mock态", radar_mode() == "mock",
               str(radar_mode()))

        # 造一个必命中 mock 发现的档案(idDigest 哈希控制)
        # 尝试多个证件号找出 h%4==0 的(行政处罚命中)
        target = None
        for i in range(50):
            p = await ps.create_role("person", f"雷达{i}",
                                     f"ID-RADAR-{i:03d}")
            d = p.get("idDigestMasked")  # 仅供日志
            from repositories.trust_value_repository import (
                id_digest,
            )
            h = int(id_digest(f"ID-RADAR-{i:03d}")[:8], 16)
            if h % 4 == 0 and h % 8 != 0 and h % 5 != 0:
                target = p
                break
        record("构造命中档案", target is not None,
               "50 个证件内未命中(概率异常)")
        if target is None:
            return
        tid = target["trustId"]
        before = (await ps.get_profile(tid))["score"]

        r = await radar.scan_public(tid)
        record("扫描200", r["success"] is True
               and r["scanned"] >= 1, str(r)[:80])
        record("发现入分", r["applied"] >= 1,
               str(r.get("applied")))

        after = (await ps.get_profile(tid))["score"]
        record("分数变动", after < before,
               f"{before} → {after}")

        # 幂等: 再扫描同档案——发现集确定性(同哈希命中),
        # 但事件已入分(重复扣分是预期: 每轮扫描是新一轮数据)
        # ——此处验证的是 scan 本身不炸且结构一致
        r2 = await radar.scan_public(tid)
        record("重复扫描结构一致",
               r2["scanned"] == r["scanned"],
               f"{r['scanned']} vs {r2['scanned']}")

        # 去标识化: 事件流水不含证件明文
        p = await ps.get_profile(tid)
        all_text = str(p.get("recentEvents"))
        record("事件去标识化", "ID-RADAR" not in all_text,
               "证件明文出现在事件")

        # 不存在档案
        try:
            await radar.scan_public(99999)
            record("扫描不存在拒绝", False, "未抛")
        except KeyError:
            record("扫描不存在拒绝", True)


class TestProbe:
    async def run(self):
        print("[06 授权探针]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_radar_service import (
            TrustRadarService,
        )
        ps = TrustProfileService()
        radar = TrustRadarService()
        p = await mk_profile(ps, "探针测试", "ID-PROBE-001")
        tid = p["trustId"]

        r = await radar.register_probe(tid, "zhima")
        record("授权登记200", r["success"] is True
               and 550 <= r["score"] <= 950, str(r)[:80])
        record("读数确定性",
               r["score"] == 550 + (tid * 31 + 5 * 7) % 401,
               str(r.get("score")))
        record("读数入分", r["applied"] is True
               and r["verified"] is True, str(r.get("applied")))

        # 授权留痕
        lst = await radar.list_probes(tid)
        record("授权留痕2条", lst["total"] == 2
               and any(x["source"] == "probe_auth"
                       for x in lst["probes"]),
               str(lst.get("total")))

        # 非法 provider
        try:
            await radar.register_probe(tid, "crawler")
            record("非法provider拒绝", False, "未抛")
        except ValueError as e:
            record("非法provider拒绝", "非法数据源" in str(e),
                   str(e))

        # 不存在档案
        try:
            await radar.register_probe(99999, "zhima")
            record("探针不存在拒绝", False, "未抛")
        except KeyError:
            record("探针不存在拒绝", True)


class TestDeposit:
    async def run(self):
        print("[07 自愿存证]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_radar_service import (
            TrustRadarService,
        )
        ps = TrustProfileService()
        radar = TrustRadarService()
        p = await mk_profile(ps, "存证测试", "ID-DEP-001")
        tid = p["trustId"]
        before = (await ps.get_profile(tid))["score"]

        # 孤证拒绝(self_deposit 单源非权威)
        r = await radar.submit_deposit(
            tid, "L3", "contribution_net", 200, 50,
            "志愿服务 200 小时(编号ZY2026-088)", "志愿服务",
            sources=["self_deposit"])
        record("孤证拒绝", r["verified"] is False
               and r["applied"] is False, str(r)[:80])
        record("拒因留痕", "孤证" in str(r.get("note", "")),
               str(r.get("note"))[:60])

        # 权威源过 + 因果净贡献
        r = await radar.submit_deposit(
            tid, "L3", "contribution_net", 200, 50,
            "志愿服务 200 小时(编号ZY2026-088, 红十字会公示)",
            "志愿服务(权威源公示)",
            sources=["gov_penalty", "media"])
        record("权威源过", r["verified"] is True
               and r["applied"] is True, str(r)[:80])
        # 净贡献 = 200 - 50×1.1 = 145; delta = min(30, 14.5)
        record("因果净贡献145", r["netContribution"] == 145.0,
               str(r.get("netContribution")))
        record("delta折算14.5", r["delta"] == 14.5,
               str(r.get("delta")))

        after = (await ps.get_profile(tid))["score"]
        record("存证提分", after > before,
               f"{before} → {after}")

        # 状态查询(applied)
        st = await radar.deposit_status(r["depositId"])
        record("状态applied", st["status"] == "applied",
               str(st.get("status")))

        # 表演式向善拦截(权威源但意图存疑)
        r2 = await radar.submit_deposit(
            tid, "L3", "contribution_net", 100, 0,
            "慈善晚宴宣传稿活动记录 2026-08-15 编号X99",
            "慈善晚宴宣传稿(品牌露出)",
            sources=["media", "self_deposit"])
        record("表演式向善拦截", r2["verified"] is False
               and r2["applied"] is False, str(r2)[:80])
        st2 = await radar.deposit_status(r2["depositId"])
        record("拒因状态rejected", st2["status"] == "rejected",
               str(st2.get("status")))

        # 参数校验
        for name, kwargs in (
                ("非法层拒绝", dict(layer="L9",
                                   factor="contribution_net")),
                ("层符不符拒绝", dict(layer="L2",
                                     factor="contribution_net")),
                ("证据过短拒绝", dict(
                    layer="L3", factor="contribution_net",
                    evidence="短")),
        ):
            base = dict(observed=100, peer_baseline=0,
                        summary="测试",
                        evidence="志愿服务 100 小时(编号ZY1)")
            base.update(kwargs)
            try:
                await radar.submit_deposit(tid, **base)
                record(name, False, "未抛")
            except ValueError:
                record(name, True)

        # 不存在档案
        try:
            await radar.submit_deposit(
                99999, "L3", "contribution_net", 100, 0,
                "志愿服务 100 小时(编号ZY2026-001)")
            record("存证不存在拒绝", False, "未抛")
        except KeyError:
            record("存证不存在拒绝", True)

        # 状态不存在
        try:
            await radar.deposit_status(99999)
            record("状态不存在拒绝", False, "未抛")
        except KeyError:
            record("状态不存在拒绝", True)


class TestNetContribution:
    async def run(self):
        print("[08 因果效应估计]")
        from services.trust_radar_service import (
            net_contribution,
        )

        record("自然增长剔除",
               net_contribution(110, 100) == 0.0,
               str(net_contribution(110, 100)))
        # 110 - 100×1.1 = 0
        record("超基线计量",
               net_contribution(200, 100) == 90.0,
               str(net_contribution(200, 100)))
        # 200 - 110 = 90
        record("零基线全额", net_contribution(50, 0) == 50.0,
               str(net_contribution(50, 0)))
        record("低于基线截断0",
               net_contribution(30, 100) == 0.0,
               str(net_contribution(30, 100)))


class TestHttp:
    async def run(self):
        print("[09 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.trust_value_routes import (
            register_trust_value_routes,
        )
        app = FastAPI()
        register_trust_value_routes(app)
        client = TestClient(app)

        # 建档
        resp = client.post("/api/trust/roles", json={
            "role": "person", "name": "HTTP雷达",
            "idNumber": "ID-HTTP-RADAR-1"})
        tid = resp.json().get("trustId")

        # 雷达扫描 200
        resp = client.post(f"/api/trust/radar/scan/{tid}")
        body = resp.json()
        record("HTTP扫描200", resp.status_code == 200
               and body.get("mode") == "mock", str(body)[:80])

        # 扫描 404
        resp = client.post("/api/trust/radar/scan/99999")
        record("HTTP扫描404", resp.status_code == 404,
               str(resp.status_code))

        # 探针登记 200
        resp = client.post("/api/trust/probes", json={
            "trustId": tid, "provider": "zhima"})
        body = resp.json()
        record("HTTP探针200", resp.status_code == 200
               and body.get("applied") is True, str(body)[:80])

        # 探针非法 provider 409
        resp = client.post("/api/trust/probes", json={
            "trustId": tid, "provider": "bad"})
        record("HTTP探针非法409", resp.status_code == 409,
               str(resp.status_code))

        # 探针列表 200
        resp = client.get(f"/api/trust/probes/{tid}")
        record("HTTP探针列表200", resp.status_code == 200
               and resp.json().get("total") == 2,
               str(resp.json().get("total")))

        # 存证孤证拒绝(留痕)
        resp = client.post("/api/trust/deposits", json={
            "trustId": tid, "layer": "L3",
            "factor": "contribution_net", "observed": 100,
            "peerBaseline": 0,
            "evidence": "志愿服务 100 小时(编号ZY2026-077)",
            "summary": "志愿服务",
            "sources": ["self_deposit"]})
        body = resp.json()
        record("HTTP存证孤证拒", resp.status_code == 200
               and body.get("verified") is False,
               str(body)[:80])

        # 存证权威源过
        resp = client.post("/api/trust/deposits", json={
            "trustId": tid, "layer": "L3",
            "factor": "contribution_net", "observed": 100,
            "peerBaseline": 0,
            "evidence": "志愿服务 100 小时(编号ZY2026-088, "
                        "红十字会公示)",
            "summary": "志愿服务(权威公示)",
            "sources": ["gov_penalty", "media"]})
        body = resp.json()
        dep_id = body.get("depositId")
        record("HTTP存证权威过", resp.status_code == 200
               and body.get("applied") is True, str(body)[:80])

        # 存证状态 200
        resp = client.get(f"/api/trust/deposits/{dep_id}/status")
        record("HTTP存证状态200", resp.status_code == 200
               and resp.json().get("status") == "applied",
               str(resp.json().get("status")))

        # 存证状态 404
        resp = client.get("/api/trust/deposits/99999/status")
        record("HTTP存证状态404", resp.status_code == 404,
               str(resp.status_code))


async def run_all():
    await TestMultimodal().run()
    await TestCrossSource().run()
    await TestIntent().run()
    await TestPipeline().run()
    await TestRadarScan().run()
    await TestProbe().run()
    await TestDeposit().run()
    await TestNetContribution().run()
    await TestHttp().run()


def main():
    asyncio.run(run_all())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
