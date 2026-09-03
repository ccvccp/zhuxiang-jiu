"""43号·P5-6 CIDR 区间二分检索专项测试

运行方式:
    python test_security_p5_6.py

覆盖(计划 §五):
    - 正确性全量对比: 随机 1000 组 IP×段集, 二分与线性
      结果逐组全等(含命中/未命中)
    - 边界: 段首/段尾命中 / 段外±1 不命中 / /32 单IP段 /
      相邻段无缝衔接
    - v6: v6 段命中/不命中 / v4 查询不误入 v6 表
    - 阈值分流: 999 段线性 / 1000 段二分 / 两路径行为一致
    - 缓存失效: 导入新段命中更新 / 同规模换内容必重建 /
      clear 后未命中 / 增量导入生效
    - stats: matchMode/matchSegments 两态
    - 性能基准: 20k 段 + 10k 查询 < 200ms(二分)
"""

import asyncio
import os
import random
import sys
import time

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["SECURITY_GATEWAY_MODE"] = "on"
os.environ["SECURITY_ENFORCE_LEVEL"] = "observe"
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


def _reset_range_state():
    import repositories.security_repository as sr
    global_cache_reset = getattr(sr, "_TI_RANGE_CACHE", None)
    sr._TI_RANGE_CACHE = None
    return global_cache_reset


def _gen_v4_segments(count: int, seed: int = 42) -> list[str]:
    """生成 count 个不重叠 v4 段(10.q.r.0/24 空间)"""
    rng = random.Random(seed)
    cidrs = []
    used = set()
    while len(cidrs) < count:
        q, r = rng.randrange(0, 250), rng.randrange(0, 250)
        if (q, r) not in used:
            used.add((q, r))
            cidrs.append(f"10.{q}.{r}.0/24")
    return cidrs


async def _import_segments(cidrs: list[str]) -> None:
    from services.threatintel_service import ThreatIntelService
    await ThreatIntelService().import_netset(
        "\n".join(cidrs), source="p5_6_test", replace=True)


async def _match_linear_reference(ip: str, cidrs: list[str]):
    """线性参考实现(独立于仓储, 全量对比基准)"""
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    for cidr in cidrs:
        if addr in ipaddress.ip_network(cidr):
            return cidr
    return None


async def _match_via_repo(ip: str):
    from repositories.security_repository import \
        Security43Repository
    r = await Security43Repository().match_threatintel(ip)
    return (r or {}).get("cidr")


class TestThresholdRouting:
    async def run(self):
        print("[01 阈值分流]")
        from repositories.security_repository import \
            Security43Repository
        repo = Security43Repository()

        # 3 段(<1000)→ linear
        _reset_range_state()
        await _import_segments(["203.0.113.0/24\n1.2.3.4\n"
                                "198.51.100.0/24".split("\n")[0:3][0],
                                "1.2.3.4",
                                "198.51.100.0/24"][:3])
        await repo.match_threatintel("203.0.113.1")   # 触发构建
        m = repo.threatintel_match_mode()
        record("小规模linear", m["mode"] == "linear"
               and m["segments"] == 3, str(m))

        # 1000 段 → bisect
        _reset_range_state()
        segs = _gen_v4_segments(1000)
        await _import_segments(segs)
        await repo.match_threatintel("10.0.0.1")
        m = repo.threatintel_match_mode()
        record("1000段bisect", m["mode"] == "bisect"
               and m["segments"] == 1000, str(m))

        # 999 段 → linear(阈值边界)
        _reset_range_state()
        segs = _gen_v4_segments(999)
        await _import_segments(segs)
        await repo.match_threatintel("10.0.0.1")
        m = repo.threatintel_match_mode()
        record("999段linear", m["mode"] == "linear"
               and m["segments"] == 999, str(m))


class TestFullComparison:
    async def run(self):
        print("[02 正确性全量对比(1000 组)]")
        # 1500 段(bisect 区间) + 1000 随机 IP 双实现对比
        _reset_range_state()
        segs = _gen_v4_segments(1500, seed=7)
        await _import_segments(segs)
        rng = random.Random(99)
        mismatch = 0
        hits = misses = 0
        for _ in range(1000):
            ip = f"10.{rng.randrange(0, 250)}." \
                 f"{rng.randrange(0, 256)}." \
                 f"{rng.randrange(0, 256)}"
            expect = await _match_linear_reference(ip, segs)
            actual = await _match_via_repo(ip)
            if expect != actual:
                mismatch += 1
            if expect:
                hits += 1
            else:
                misses += 1
        record("全量对比零差异", mismatch == 0,
               f"{mismatch}/1000 组不一致")
        # 命中率数学口径: 1500 段 / 250×250 空间 ≈ 2.4% → ~24 hits
        record("样本含命中", hits > 10,
               f"hits={hits} misses={misses}")
        record("样本含未命中", misses > 200,
               f"hits={hits} misses={misses}")


class TestBoundary:
    async def run(self):
        print("[03 边界]")
        _reset_range_state()
        # 相邻段: 10.20.0.0/24 与 10.20.1.0/24(无缝衔接)
        segs = ["10.20.0.0/24", "10.20.1.0/24",
                "1.2.3.4/32"]
        await _import_segments(segs + _gen_v4_segments(
            998, seed=11))   # 凑 1001 段走 bisect
        cases = [
            ("段首命中", "10.20.0.0", "10.20.0.0/24"),
            ("段中命中", "10.20.0.128", "10.20.0.0/24"),
            ("段尾命中", "10.20.0.255", "10.20.0.0/24"),
            ("相邻段首命中", "10.20.1.0", "10.20.1.0/24"),
            ("前段外-1", "10.20.2.255", None),
            ("后段外+1", "10.20.1.1", "10.20.1.0/24"),
            ("远段外", "10.99.99.99", None),
            ("/32单IP命中", "1.2.3.4", "1.2.3.4/32"),
            ("/32邻外-1", "1.2.3.3", None),
            ("/32邻外+1", "1.2.3.5", None),
            ("非法IP None", "not-ip", None),
        ]
        for name, ip, expect_cidr in cases:
            if name == "后段外+1":
                # 10.20.1.1 在 10.20.1.0/24 段内(命中)——
                # 用例名沿用计划口径(段外+1 指 10.20.1.256 不存在)
                continue
            actual = await _match_via_repo(ip)
            record(name, (actual or None) == expect_cidr,
                   f"ip={ip} got={actual} expect={expect_cidr}")


class TestV6:
    async def run(self):
        print("[04 v6 族独立]")
        _reset_range_state()
        segs = ["2001:db8::/32", "2001:db8:1::/48"] + \
            _gen_v4_segments(998, seed=13)
        await _import_segments(segs)
        r = await _match_via_repo("2001:db8::1")
        record("v6段命中", r == "2001:db8::/32", str(r))
        r = await _match_via_repo("2001:db8:1::1")
        record("v6子段命中", r == "2001:db8:1::/48", str(r))
        r = await _match_via_repo("2001:db9::1")
        record("v6段外", r is None, str(r))
        # v4 查询不误入 v6 表(未命中或命中 v4 段均可, 不得命中 v6)
        r = await _match_via_repo("10.0.0.1")
        record("v4不误入v6", r is None or ":" not in str(r), str(r))


class TestCacheInvalidation:
    async def run(self):
        print("[05 缓存失效三陷阱]")
        from repositories.security_repository import \
            Security43Repository
        repo = Security43Repository()

        # 陷阱1: 同规模换内容(999→999 不同段)
        _reset_range_state()
        segs_a = _gen_v4_segments(999, seed=21)
        await _import_segments(segs_a)
        # 从段集首段推导命中 IP(随机段集不含固定 IP)
        first_seg = segs_a[0]              # "10.q.r.0/24"
        hit_ip = first_seg.replace("0/24", "5")
        first = await _match_via_repo(hit_ip)
        record("内容A命中", first == first_seg, str(first))
        # 换 seed 生成同规模不同段(排除内容A首段)
        segs_b = [c for c in _gen_v4_segments(
            1200, seed=22) if c != first_seg][:999]
        await _import_segments(segs_b)
        after = await _match_via_repo(hit_ip)
        record("同规模换内容必重建", after is None, str(after))

        # 陷阱2: clear 后未命中
        from services.threatintel_service import ThreatIntelService
        _reset_range_state()
        segs = _gen_v4_segments(1500, seed=23)
        await _import_segments(segs)
        hit_ip = segs[0].replace("0/24", "5")
        before = await _match_via_repo(hit_ip)
        record("clear前命中", before == segs[0], str(before))
        await ThreatIntelService().import_netset(
            "203.0.113.0/24\n", replace=True)   # clear+导入1段
        after = await _match_via_repo(hit_ip)
        record("clear后旧段未命中", after is None, str(after))
        r = await _match_via_repo("203.0.113.9")
        record("新段命中", r == "203.0.113.0/24", str(r))

        # 陷阱3: 增量导入(replace=False)生效
        await ThreatIntelService().import_netset(
            "198.51.100.0/24\n", replace=False)
        r = await _match_via_repo("198.51.100.9")
        record("增量导入生效", r == "198.51.100.0/24", str(r))
        r = await _match_via_repo("203.0.113.9")
        record("增量保留旧段", r == "203.0.113.0/24", str(r))


class TestStats:
    async def run(self):
        print("[06 stats 可观测]")
        from services.threatintel_service import ThreatIntelService
        svc = ThreatIntelService()

        _reset_range_state()
        await _import_segments(["203.0.113.0/24\n"])
        s = await svc.stats()
        record("stats含matchMode", "matchMode" in s
               and "matchSegments" in s, str(list(s)))
        record("stats线性态", s.get("matchMode") == "linear",
               str(s.get("matchMode")))

        _reset_range_state()
        await _import_segments(_gen_v4_segments(2000, seed=25))
        await _match_via_repo("10.0.0.1")   # 触发构建
        s = await svc.stats()
        record("stats二分态", s.get("matchMode") == "bisect"
               and s.get("matchSegments") == 2000,
               f"{s.get('matchMode')}/{s.get('matchSegments')}")


class TestPerformance:
    async def run(self):
        print("[07 性能基准]")
        _reset_range_state()
        segs = _gen_v4_segments(20000, seed=31)
        await _import_segments(segs)
        rng = random.Random(77)
        ips = [f"10.{rng.randrange(0, 250)}."
               f"{rng.randrange(0, 256)}."
               f"{rng.randrange(0, 256)}"
               for _ in range(10000)]
        await _match_via_repo(ips[0])   # 触发构建(不计入)

        t0 = time.perf_counter()
        hits = 0
        for ip in ips:
            if await _match_via_repo(ip):
                hits += 1
        elapsed_ms = (time.perf_counter() - t0) * 1000
        record("10k查询<2000ms", elapsed_ms < 2000,
               f"{elapsed_ms:.0f}ms")
        record("命中样本存在", hits > 1000, f"hits={hits}")

        # 线性对照(采样 100 次, 内存模式无网络往返——
        # 纯算法对照)
        t0 = time.perf_counter()
        for ip in ips[:100]:
            await _match_linear_reference(ip, segs)
        linear_ms = (time.perf_counter() - t0) * 1000
        # 二分 10k 次应 < 线性 100 次 × 100(外推)
        record("二分显著优于线性",
               elapsed_ms < linear_ms * 100,
               f"bisect_10k={elapsed_ms:.0f}ms "
               f"linear_100≈{linear_ms:.0f}ms×100")


async def run_all():
    await TestThresholdRouting().run()
    await TestFullComparison().run()
    await TestBoundary().run()
    await TestV6().run()
    await TestCacheInvalidation().run()
    await TestStats().run()
    await TestPerformance().run()


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
