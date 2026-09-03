"""43号·P6-1 情报源聚合与规模扩展专项测试

运行方式:
    python test_security_p6_1.py

覆盖(计划 §六):
    - 按源替换: A源导入→B源导入→A段保留 / 同源刷新只清
      本源 / clear(None) 全清兼容 / 增量导入保留
    - 批量写: 内存批量=逐条等价(数与查询一致)
    - 键过滤: list 不含 auto/scheduler 单例键
    - 上限变量: 默认 20000 兼容 / 提额生效 / 非法回退
    - 计数器: 导入增量维护 / 同源刷新差值 / stats 计数器
      直读 / 兜底重建(删计数器→恢复)
    - 多源 feed: URL 解析(name 推导/显式 name/容错) /
      单 URL 回退兼容 / 每源独立状态 / 单源失败不阻断 /
      degraded_sources 聚合
    - 调度器: 多源遍历汇总 / 单源失败容错
    - S2 按源触达: 多源双降级两告警(rule 含源名)/
      未达阈不告警 / 单源回退口径不变
    - 回归: P5-3 单源口径 / P5-6 匹配正确性联动
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
os.environ["GEOIP_DB_PATH"] = "/nonexistent/GeoLite2-City.mmdb"
os.environ.pop("SECURITY_THREATINTEL_MAX_CIDRS", None)
os.environ.pop("SECURITY_THREATINTEL_URLS", None)
os.environ.pop("SECURITY_THREATINTEL_URL", None)

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


def _clear_range_state():
    import repositories.security_repository as sr
    sr._TI_RANGE_CACHE = None


def _clear_srcstats():
    from repositories.backend import get_in_memory_store
    store = get_in_memory_store()
    store.pop("_security43_srcstats", None)


class TestSourceScopedReplace:
    async def run(self):
        print("[01 按源替换]")
        from services.threatintel_service import ThreatIntelService
        from repositories.security_repository import \
            Security43Repository
        svc = ThreatIntelService()
        repo = Security43Repository()
        _clear_range_state()
        _clear_srcstats()

        # A 源(150 段)
        segs_a = [f"10.{i // 250}.{i % 250}.0/24"
                  for i in range(150)]
        await svc.import_netset("\n".join(segs_a),
                                source="firehol_level1")
        s = await svc.stats()
        record("A源导入", s["totalCidrs"] == 150
               and s["sources"].get("firehol_level1") == 150,
               str(s["sources"]))

        # B 源导入 → A 段保留
        segs_b = [f"172.16.{i}.0/24" for i in range(150)]
        await svc.import_netset("\n".join(segs_b),
                                source="firehol_level2")
        s = await svc.stats()
        record("B源A段保留", s["totalCidrs"] == 300
               and s["sources"].get("firehol_level1") == 150
               and s["sources"].get("firehol_level2") == 150,
               str(s["sources"]))
        r = await svc.check_ip("10.0.0.1")
        record("A源命中仍有效", (r or {}).get("cidr")
               == "10.0.0.0/24", str(r))

        # 同源刷新只清本源(B 换内容)
        segs_b2 = [f"192.168.{i}.0/24" for i in range(150)]
        r = await svc.import_netset("\n".join(segs_b2),
                                     source="firehol_level2")
        record("同源刷新cleared=150", r["cleared"] == 150,
               str(r))
        s = await svc.stats()
        record("刷新后A段不受影响", s["totalCidrs"] == 300
               and s["sources"].get("firehol_level1") == 150,
               str(s["sources"]))
        record("B旧段已换", await svc.check_ip(
            "172.16.0.1") is None, "应 None")
        record("B新段生效", (await svc.check_ip(
            "192.168.1.1") or {}).get("cidr") == "192.168.1.0/24")

        # clear(None) 全清兼容
        cleared = await repo.clear_threatintel()
        record("全清兼容", cleared == 300, str(cleared))
        s = await svc.stats()
        record("全清后计数器归零", s["totalCidrs"] == 0,
               str(s))

        # 增量导入保留(replace=False)
        await svc.import_netset("203.0.113.0/24\n",
                                source="firehol_level1")
        await svc.import_netset("198.51.100.0/24\n",
                                source="firehol_level1",
                                replace=False)
        s = await svc.stats()
        record("增量保留", s["totalCidrs"] == 2
               and s["sources"].get("firehol_level1") == 2,
               str(s["sources"]))


class TestBatchWrite:
    async def run(self):
        print("[02 批量写等价]")
        from services.threatintel_service import ThreatIntelService
        from repositories.security_repository import \
            Security43Repository
        svc = ThreatIntelService()
        repo = Security43Repository()
        _clear_range_state()
        _clear_srcstats()

        segs = [f"10.{i // 250}.{i % 250}.0/24"
                for i in range(2000)]
        await repo.clear_threatintel()   # 隔离上一用例残留段
        r = await svc.import_netset("\n".join(segs),
                                   source="batch_test")
        record("批量导入2000", r["imported"] == 2000, str(r))
        records = await repo.list_threatintel()
        record("批量读数一致", len(records) == 2000,
               f"{len(records)}")
        # 键过滤: 不含单例键(auto/srcstats 不入段列表)
        non_seg = [r for r in records
                   if not str(r.get("actorKey", "")
                              ).startswith("threatintel:")]
        record("键过滤无单例", len(non_seg) == 0, str(non_seg)[:80])
        # 匹配正确性(P5-6 联动)
        r = await svc.check_ip("10.0.0.99")
        record("批量后匹配正确", (r or {}).get("cidr")
               == "10.0.0.0/24", str(r))


class TestMaxEnv:
    async def run(self):
        print("[03 上限环境变量]")
        import services.threatintel_service as tis
        from services.threatintel_service import (
            ThreatIntelService, max_import_cidrs,
        )
        record("默认20000", max_import_cidrs() == 20000,
               str(max_import_cidrs()))

        os.environ["SECURITY_THREATINTEL_MAX_CIDRS"] = "30000"
        record("提额生效", max_import_cidrs() == 30000,
               str(max_import_cidrs()))
        # 25000 段可导(原上限会拒)
        segs = [f"10.{a}.{b}.0/24"
                for a in range(100) for b in range(250)]
        _clear_range_state()
        _clear_srcstats()
        r = await ThreatIntelService().import_netset(
            "\n".join(segs), source="over20000")
        record("25000段可导", r["imported"] == 25000,
               str(r["imported"]))

        os.environ["SECURITY_THREATINTEL_MAX_CIDRS"] = "bad"
        record("非法回退", max_import_cidrs() == 20000,
               str(max_import_cidrs()))
        os.environ["SECURITY_THREATINTEL_MAX_CIDRS"] = "500"
        record("下限1000抬升", max_import_cidrs() == 1000,
               str(max_import_cidrs()))

        # 超上限拒绝(1500 段 > 1000; 两段位展开保证全部有效)
        os.environ["SECURITY_THREATINTEL_MAX_CIDRS"] = "1000"
        try:
            segs = [f"10.{i // 250}.{i % 250}.0/24"
                    for i in range(1500)]
            await ThreatIntelService().import_netset(
                "\n".join(segs), source="over_limit")
            record("超上限拒绝", False, "未抛")
        except ValueError:
            record("超上限拒绝", True)
        os.environ.pop("SECURITY_THREATINTEL_MAX_CIDRS", None)


class TestCounter:
    async def run(self):
        print("[04 计数器]")
        from services.threatintel_service import ThreatIntelService
        from repositories.security_repository import \
            Security43Repository
        svc = ThreatIntelService()
        _clear_range_state()
        _clear_srcstats()
        await Security43Repository().clear_threatintel()  # 隔离残留

        await svc.import_netset(
            "\n".join(f"10.{i}.0.0/16" for i in range(100)),
            source="cnt_a")
        await svc.import_netset(
            "\n".join(f"11.{i}.0.0/16" for i in range(50)),
            source="cnt_b")
        s = await svc.stats()
        record("计数器直读", s["totalCidrs"] == 150
               and s["sources"] == {"cnt_a": 100, "cnt_b": 50},
               str(s["sources"]))

        # 同源刷新差值(100→30)
        await svc.import_netset(
            "\n".join(f"12.{i}.0.0/16" for i in range(30)),
            source="cnt_a")
        s = await svc.stats()
        record("刷新差值", s["sources"].get("cnt_a") == 30
               and s["totalCidrs"] == 80, str(s["sources"]))

        # 兜底重建(删计数器 → stats 恢复)
        _clear_srcstats()
        s = await svc.stats()
        record("兜底重建", s["totalCidrs"] == 80
               and s["sources"].get("cnt_a") == 30,
               str(s["sources"]))
        s2 = await svc.stats()
        record("重建后毫秒级", s2["totalCidrs"] == 80,
               str(s2["sources"]))


class TestFeedSources:
    async def run(self):
        print("[05 多源 feed 解析]")
        import services.threatintel_feed as feed
        from services.threatintel_service import ThreatIntelService

        # 单 URL 回退兼容(P5-3)
        os.environ.pop("SECURITY_THREATINTEL_URLS", None)
        os.environ.pop("SECURITY_THREATINTEL_URL", None)
        srcs = feed.feed_sources()
        record("缺省单源firehol_level1",
               len(srcs) == 1
               and srcs[0]["name"] == "firehol_level1",
               str(srcs))
        # 自定义单 URL → 文件名推导
        os.environ["SECURITY_THREATINTEL_URL"] = \
            "http://mirror.cn/firehol_level2.netset"
        srcs = feed.feed_sources()
        record("单URL文件名推导", srcs[0]["name"]
               == "firehol_level2", str(srcs))
        os.environ.pop("SECURITY_THREATINTEL_URL")

        # URLS 多源(name 显式+省略推导)
        os.environ["SECURITY_THREATINTEL_URLS"] = (
            "srcA=http://x.com/a.netset,"
            "http://y.com/firehol_cross.netset,"
            ",bad-empty=http://z.com/c.netset,")
        srcs = feed.feed_sources()
        names = [s["name"] for s in srcs]
        record("多源解析3源", len(srcs) == 3, str(names))
        record("显式name", "srcA" in names, str(names))
        record("推导name", "firehol_cross" in names, str(names))
        record("空part容错", len(srcs) == 3, str(names))
        os.environ.pop("SECURITY_THREATINTEL_URLS")

        # name 推导回退(host)
        record("host回退",
               feed._source_name_from_url(
                   "http://plain.host/") == "plain_host",
               feed._source_name_from_url("http://plain.host/"))

        # 每源独立状态 + 单源失败不阻断
        async def _fail_fetch(url=None, timeout=None):
            raise ValueError("模拟失败")
        async def _ok_fetch(url=None, timeout=None):
            return "\n".join(f"10.{i}.0.0/16"
                             for i in range(150))
        orig = feed.fetch_netset

        os.environ["SECURITY_THREATINTEL_URLS"] = (
            "good=http://g.com/g.netset,"
            "poison=http://p.com/p.netset")
        _clear_range_state()
        _clear_srcstats()
        # poison 源失败
        feed.fetch_netset = _fail_fetch
        r = await feed.maybe_refresh(
            source={"name": "poison",
                    "url": "http://p.com/p.netset"}, force=True)
        record("单源失败计数", r["consecutiveFailures"] == 1
               and r["status"] == "failed", str(r))
        # good 源成功(不受 poison 影响)
        feed.fetch_netset = _ok_fetch
        r = await feed.maybe_refresh(
            source={"name": "good",
                    "url": "http://g.com/g.netset"}, force=True)
        record("其余源正常导入", r["executed"] is True
               and r["imported"] == 150, str(r))
        s = await ThreatIntelService().stats()
        record("good源段入库", s["sources"].get("good") == 150,
               str(s["sources"]))

        # degraded_sources 聚合
        poison_state = await feed._load_auto_state(
            {"name": "poison"})
        record("poison独立状态", poison_state.get(
            "consecutiveFailures") == 1, str(poison_state))
        d = await feed.degraded_sources()
        record("降级聚合未达阈", d["any"] is False,
               str(d["degradedSources"]))
        # poison 再失败 2 次(共 3) → degraded
        feed.fetch_netset = _fail_fetch
        for _ in range(2):
            await feed.maybe_refresh(
                source={"name": "poison",
                        "url": "http://p.com/p.netset"}, force=True)
        d = await feed.degraded_sources()
        record("降级聚合达阈", d["any"] is True
               and "poison" in d["degradedSources"]
               and "good" not in d["degradedSources"],
               str(d["degradedSources"]))
        feed.fetch_netset = orig
        os.environ.pop("SECURITY_THREATINTEL_URLS")

        # 单源兼容(maybe_refresh 无 source → P5-3 键)
        feed.fetch_netset = _ok_fetch
        r = await feed.maybe_refresh(force=True)
        record("单源兼容source", r.get("source") is None
               and r["executed"] is True, str(r)[:100])
        from repositories.security_repository import \
            Security43Repository
        compat = await Security43Repository(
        ).get_threatintel_auto_state()   # 单源键
        record("单源兼容键", compat and compat.get(
            "lastAutoStatus") == "ok", str(compat)[:80])
        feed.fetch_netset = orig


class TestSchedulerMultiSource:
    async def run(self):
        print("[06 调度器多源遍历]")
        import services.threatintel_feed as feed

        # mock 双源: good 成功 + poison 失败
        calls = {"poison": 0}

        async def _mixed_fetch(url=None, timeout=None):
            if "poison" in str(url):
                calls["poison"] += 1
                raise ValueError("毒源")
            return "\n".join(f"10.{i}.0.0/16"
                             for i in range(150))
        orig = feed.fetch_netset
        os.environ["SECURITY_THREATINTEL_AUTO"] = "on"
        # 新源名(独立 auto 状态) + poison URL 含关键字
        # (mock 按 URL 判定毒源)
        os.environ["SECURITY_THREATINTEL_URLS"] = (
            "sched_good=http://good.test/g.netset,"
            "sched_poison=http://poison.test/p.netset")
        _clear_range_state()
        _clear_srcstats()
        feed.fetch_netset = _mixed_fetch

        from services.security_scheduler import (
            run_scheduled_security_tasks,
        )
        stats = await run_scheduled_security_tasks()
        ti = stats.get("lastThreatintel") or {}
        record("多源汇总sources=2", ti.get("sources") == 2,
               str(ti))
        record("executed=1 failed=1", ti.get("executed") == 1
               and ti.get("failed") == 1, str(ti))
        record("毒源未阻断调度", stats.get("lastBaselines")
               is not None, "基线正常")
        record("毒源错误留痕", any("poison" in str(e)
                                   for e in stats.get(
                                       "lastErrors", [])),
               str(stats.get("lastErrors")))

        feed.fetch_netset = orig
        os.environ.pop("SECURITY_THREATINTEL_AUTO")
        os.environ.pop("SECURITY_THREATINTEL_URLS")


class TestS2PerSource:
    async def run(self):
        print("[07 S2 信号按源触达]")
        from repositories.security_repository import \
            Security43Repository
        repo = Security43Repository()

        # 多源模式: 两个源均降级 → 两条独立告警(rule 含源名)
        os.environ["SECURITY_THREATINTEL_URLS"] = (
            "srcA=http://a.test/a.netset,"
            "srcB=http://b.test/b.netset")
        await repo.save_threatintel_auto_state({
            "lastAutoImportAt": "", "lastAutoStatus": "failed",
            "consecutiveFailures": 3,
            "lastError": "srcA 网络超时"}, source="srcA")
        await repo.save_threatintel_auto_state({
            "lastAutoImportAt": "", "lastAutoStatus": "failed",
            "consecutiveFailures": 4,
            "lastError": "srcB 拒绝连接"}, source="srcB")
        from services.security_alert_service import (
            SecurityAlertService,
        )
        alerts = await SecurityAlertService(
        )._collect_intel_degraded()
        rules = sorted(a["rule"] for a in alerts)
        record("多源双降级两告警", len(alerts) == 2,
               str(rules))
        record("rule含源名",
               rules == ["threatintel_degraded:srcA",
                         "threatintel_degraded:srcB"],
               str(rules))
        record("消息含源名", all(
            f"威胁情报源 {n}" in a["message"]
            for a, n in zip(sorted(alerts,
                                   key=lambda x: x["rule"]),
                            ("srcA", "srcB"))),
            str([a["message"] for a in alerts])[:150])
        record("lastError透传", any(
            "拒绝连接" in a["message"] for a in alerts),
            str([a["message"] for a in alerts])[:150])

        # 未降级源不告警 + 单源回退口径不变
        await repo.save_threatintel_auto_state({
            "lastAutoImportAt": "", "lastAutoStatus": "failed",
            "consecutiveFailures": 1,
            "lastError": "srcA 偶发"}, source="srcA")
        alerts = await SecurityAlertService(
        )._collect_intel_degraded()
        record("未达阈不告警", len(alerts) == 1
               and alerts[0]["rule"].endswith(":srcB"),
               str([a["rule"] for a in alerts]))

        os.environ.pop("SECURITY_THREATINTEL_URLS")
        await repo.save_threatintel_auto_state({
            "lastAutoImportAt": "", "lastAutoStatus": "failed",
            "consecutiveFailures": 3,
            "lastError": "单源失败"}, source=None)
        alerts = await SecurityAlertService(
        )._collect_intel_degraded()
        record("单源回退口径", len(alerts) == 1
               and alerts[0]["rule"] == "threatintel_degraded"
               and "威胁情报订阅连续失败" in alerts[0]["message"],
               str(alerts))
        # 清理降级状态(避免影响后续 stats auto 断言)
        await repo.save_threatintel_auto_state({
            "lastAutoImportAt": "", "lastAutoStatus": "",
            "consecutiveFailures": 0, "lastError": ""})
        await repo.save_threatintel_auto_state({
            "lastAutoImportAt": "", "lastAutoStatus": "",
            "consecutiveFailures": 0, "lastError": ""},
            source="srcA")
        await repo.save_threatintel_auto_state({
            "lastAutoImportAt": "", "lastAutoStatus": "",
            "consecutiveFailures": 0, "lastError": ""},
            source="srcB")


class TestP53Regression:
    async def run(self):
        print("[08 P5-3 单源口径回归]")
        import services.threatintel_feed as feed

        async def _ok_fetch(url=None, timeout=None):
            return "\n".join(f"10.{i // 250}.{i % 250}.0/24"
                             for i in range(150))
        orig = feed.fetch_netset
        feed.fetch_netset = _ok_fetch
        os.environ.pop("SECURITY_THREATINTEL_URLS", None)

        # 无 source → 单源导入(source=firehol_level1_auto)
        r = await feed.maybe_refresh(force=True)
        record("单源refresh兼容", r["executed"] is True
               and r["imported"] == 150, str(r)[:100])
        from services.threatintel_service import ThreatIntelService
        s = await ThreatIntelService().stats()
        record("单源auto状态", s["auto"].get("lastAutoStatus")
               == "ok", str(s["auto"]))
        record("单源degraded", s["auto"].get("degraded")
               is False, str(s["auto"]))
        feed.fetch_netset = orig


async def run_all():
    await TestSourceScopedReplace().run()
    await TestBatchWrite().run()
    await TestMaxEnv().run()
    await TestCounter().run()
    await TestFeedSources().run()
    await TestSchedulerMultiSource().run()
    await TestS2PerSource().run()
    await TestP53Regression().run()
    os.environ.pop("SECURITY_THREATINTEL_MAX_CIDRS", None)
    os.environ.pop("SECURITY_THREATINTEL_URLS", None)


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
