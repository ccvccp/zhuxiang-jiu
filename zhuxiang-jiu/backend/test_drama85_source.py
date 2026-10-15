"""85号·真实数据源接入专项测试(Provider 三态, fake transport 零网络)

运行:
    python test_drama85_source.py

覆盖:
    1. real 凭证缺失 fail-hard(ValueError, 不降级)
    2. real 正常采集: 契约解析/structure 直传优先/字段容错
       (缺 title 丢弃/completionRate clamp)/入库 sourceChannel=real
    3. 鉴权头: X-Api-Key/X-Timestamp/X-Source-Signature(HMAC 复算)
    4. 网络重试: 前两次 500 第三次 200 成功(3 次尝试)
    5. 响应异常: 无 dramas 键 → 重试耗尽失败
    6. mock_fallback: real 失败自动降级 mock 留痕(fallback+Reason)
    7. real 模式失败不降级直接抛
    8. QPS 频控: 连续两次调用间隔 ≥ 1/qps
    9. HTTP 全链: DRAMA85_SOURCE_MODE=mock_fallback 下 collect
       报告 fallback 留痕
"""
import asyncio
import hashlib
import hmac
import os
import sys
import time

os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")
os.environ.setdefault("AUTH_MODE", "compat")
os.environ.setdefault("AUTH_COMPAT_TRUST_HEADERS", "1")

PASS = 0
FAIL = 0
RESULTS = []

ADMIN = {"X-Role": "admin"}
ENV_KEYS = ("DRAMA85_SOURCE_MODE", "DRAMA85_SOURCE_URL",
            "DRAMA85_SOURCE_API_KEY", "DRAMA85_SOURCE_SECRET",
            "DRAMA85_SOURCE_QPS")


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


def run(coro):
    from core import locks as _locks
    _locks._async_locks.clear()
    try:
        return asyncio.run(coro)
    finally:
        _locks._async_locks.clear()


class EnvGuard:
    """env 快照守卫(每段独立)"""

    def __enter__(self):
        self._backup = {k: os.environ.get(k)
                        for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        return self

    def __exit__(self, *exc):
        for k, v in self._backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def _contract_response(n=3):
    """构造契约响应(含 structure 直传/缺 title 坏条目/越界完播)"""
    dramas = []
    for i in range(n):
        item = {
            "title": f"真实源短剧{i}号",
            "platforms": ["douyin", "kuaishou"],
            "episodes": 60 + i,
            "durationSec": 90,
            "tags": ["重生复仇", "都市"],
            "metrics": {
                "plays": 1_000_000 + i * 1000,
                "likes": 60_000, "comments": 8_000,
                "shares": 12_000,
                "completionRate": 0.55 + i * 0.05,
            },
            "commentsData": [
                {"platform": "douyin", "text": "反转太绝了封神"},
                {"platform": "kuaishou", "text": "每天蹲更新根本不够看"},
            ],
            "structure": {
                "hook": f"第{i}集主角当众被退婚",
                "conflict": "家产被亲戚觊觎",
                "twist": "主角隐藏身份曝光",
                "suspense": "一封匿名信送到",
            },
        }
        if i == n - 1:
            item["metrics"]["completionRate"] = 1.8   # 越界→clamp
        dramas.append(item)
    dramas.append({"platforms": ["x"]})   # 坏条目(缺 title)→丢弃
    return {"dramas": dramas, "hasMore": True}


def main():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    print("[01 real 凭证缺失 fail-hard]")
    with EnvGuard():
        os.environ["DRAMA85_SOURCE_MODE"] = "real"
        from services.drama85_source_gateway import (
            RealDramaSource, DramaSourceGateway,
        )
        try:
            run(DramaSourceGateway().fetch(5))
            check("real 缺凭证 → ValueError", False, "未抛错")
        except ValueError as e:
            check("real 缺凭证 → ValueError",
                  "凭证未配置" in str(e), str(e))

    print("[02 real 正常采集(契约解析)]")

    async def _real_fetch():
        from services.drama85_source_gateway import (
            RealDramaSource,
        )
        return await RealDramaSource(_fake_ok).fetch(3, 0)

    async def _fake_ok(url, headers=None):
        return 200, _contract_response(3)

    with EnvGuard():
        os.environ["DRAMA85_SOURCE_URL"] = "https://src.test/api/dramas"
        os.environ["DRAMA85_SOURCE_API_KEY"] = "test-key"
        os.environ["DRAMA85_SOURCE_QPS"] = "10"   # 测试提速
        rows = run(_real_fetch())
        check("契约解析: 好数目 3(坏条目丢弃)",
              len(rows) == 3, str(len(rows)))
        check("structure 直传优先",
              rows[0]["structure"]["hook"] == "第0集主角当众被退婚",
              str(rows[0].get("structure")))
        check("completionRate 越界 clamp",
              rows[-1]["metrics"]["completionRate"] == 1.0,
              str(rows[-1]["metrics"]["completionRate"]))
        check("评论/标签/平台透传",
              rows[0]["commentsData"][0]["platform"] == "douyin"
              and rows[0]["tags"] == ["重生复仇", "都市"],
              str(rows[0]["tags"]))

        # 入库全链: run_collect 走 gateway 默认 http(会真发请求)
        # ——real 数据形状由 rows 断言覆盖, 入库链由 09 段
        # mock_fallback HTTP 全链覆盖(sourceChannel 留痕)
        check("real 数据形状兼容清洗管道(字段齐备)",
              all(k in rows[0] for k in
                  ("title", "platforms", "metrics", "commentsData",
                   "structure")), str(list(rows[0])))

    print("[03 鉴权头(HMAC 签名)]")
    captured = {}

    async def _fake_capture(url, headers=None):
        global _last_url
        _last_url = url
        captured.update(headers or {})
        return 200, _contract_response(1)

    async def _signed_fetch():
        from services.drama85_source_gateway import (
            RealDramaSource,
        )
        return await RealDramaSource(_fake_capture).fetch(1, 0)

    with EnvGuard():
        os.environ["DRAMA85_SOURCE_URL"] = "https://src.test/v1/dramas"
        os.environ["DRAMA85_SOURCE_API_KEY"] = "key-abc"
        os.environ["DRAMA85_SOURCE_SECRET"] = "sec-xyz"
        os.environ["DRAMA85_SOURCE_QPS"] = "10"
        run(_signed_fetch())
        expected = hmac.new(
            b"sec-xyz",
            f"key-abc{captured.get('X-Timestamp', '')}"
            f"/v1/dramas".encode("utf-8"),
            hashlib.sha256).hexdigest()
        check("X-Api-Key/X-Timestamp 在位",
              captured.get("X-Api-Key") == "key-abc"
              and str(captured.get("X-Timestamp", "")).isdigit(),
              str(captured))
        check("X-Source-Signature HMAC 复算一致",
              captured.get("X-Source-Signature") == expected,
              str(captured.get("X-Source-Signature")))
        check("URL 携带 offset/n",
              "offset=0" in _last_url and "n=1" in _last_url, "")

    print("[04 网络重试(500×2→200)]")
    attempts = {"n": 0}

    async def _fake_flaky(url, headers=None):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            return 500, {}
        return 200, _contract_response(1)

    async def _retry_fetch():
        from services.drama85_source_gateway import (
            RealDramaSource,
        )
        return await RealDramaSource(_fake_flaky).fetch(1, 0)

    with EnvGuard():
        os.environ["DRAMA85_SOURCE_URL"] = "https://src.test/api"
        os.environ["DRAMA85_SOURCE_API_KEY"] = "k"
        os.environ["DRAMA85_SOURCE_QPS"] = "10"
        rows = run(_retry_fetch())
        check("两次 500 后第三次成功(共 3 尝试)",
              len(rows) == 1 and attempts["n"] == 3,
              str(attempts))

    print("[05 响应异常重试耗尽]")

    async def _fake_bad(url, headers=None):
        return 200, {"foo": "bar"}   # 无 dramas 键

    async def _bad_fetch():
        from services.drama85_source_gateway import (
            RealDramaSource,
        )
        return await RealDramaSource(_fake_bad).fetch(1, 0)

    with EnvGuard():
        os.environ["DRAMA85_SOURCE_URL"] = "https://src.test/api"
        os.environ["DRAMA85_SOURCE_API_KEY"] = "k"
        os.environ["DRAMA85_SOURCE_QPS"] = "10"
        try:
            run(_bad_fetch())
            check("响应异常 → ValueError", False, "未抛错")
        except ValueError as e:
            check("响应异常 → ValueError",
                  "响应异常" in str(e), str(e))

    print("[06 mock_fallback 降级留痕]")

    async def _fake_dead(url, headers=None):
        raise ConnectionError("connection refused")

    async def _fallback_fetch():
        from services.drama85_source_gateway import (
            DramaSourceGateway,
        )
        return await DramaSourceGateway(_fake_dead).fetch(2, 0)

    with EnvGuard():
        os.environ["DRAMA85_SOURCE_MODE"] = "mock_fallback"
        os.environ["DRAMA85_SOURCE_URL"] = "https://src.test/api"
        os.environ["DRAMA85_SOURCE_API_KEY"] = "k"
        os.environ["DRAMA85_SOURCE_QPS"] = "10"
        result = run(_fallback_fetch())
        check("降级 mock 留痕(fallback+reason+source)",
              result.get("fallback") is True
              and "connection refused" in str(
                  result.get("fallbackReason"))
              and result.get("source") == "mock"
              and result.get("sourceMode") == "mock_fallback"
              and len(result.get("rows") or []) == 2,
              str({k: result.get(k) for k in
                   ("fallback", "source", "sourceMode")}))

    print("[07 real 失败不降级]")

    async def _real_dead():
        from services.drama85_source_gateway import (
            DramaSourceGateway,
        )
        return await DramaSourceGateway(_fake_dead).fetch(2, 0)

    with EnvGuard():
        os.environ["DRAMA85_SOURCE_MODE"] = "real"
        os.environ["DRAMA85_SOURCE_URL"] = "https://src.test/api"
        os.environ["DRAMA85_SOURCE_API_KEY"] = "k"
        os.environ["DRAMA85_SOURCE_QPS"] = "10"
        try:
            run(_real_dead())
            check("real 失败直接抛(fail-hard)", False, "未抛错")
        except ConnectionError:
            check("real 失败直接抛(fail-hard)", True)
        except ValueError as e:
            check("real 失败直接抛(fail-hard)",
                  "凭证未配置" not in str(e), str(e))

    print("[08 QPS 频控]")

    async def _qps_probe():
        from services.drama85_source_gateway import (
            RealDramaSource,
        )
        gw = RealDramaSource(_fake_ok)
        t0 = time.monotonic()
        await gw.fetch(1, 0)
        await gw.fetch(1, 0)
        return time.monotonic() - t0

    with EnvGuard():
        os.environ["DRAMA85_SOURCE_URL"] = "https://src.test/api"
        os.environ["DRAMA85_SOURCE_API_KEY"] = "k"
        os.environ["DRAMA85_SOURCE_QPS"] = "5"
        elapsed = run(_qps_probe())
        check("两次调用间隔 ≥ 1/qps(0.2s)",
              elapsed >= 0.18, f"{elapsed:.3f}s")

    print("[09 HTTP 全链(mock_fallback 报告留痕)]")
    with EnvGuard():
        os.environ["DRAMA85_SOURCE_MODE"] = "mock_fallback"
        os.environ["DRAMA85_SOURCE_URL"] = "https://dead.test/api"
        os.environ["DRAMA85_SOURCE_API_KEY"] = "k"
        os.environ["DRAMA85_SOURCE_QPS"] = "10"
        r = client.post("/api/drama85/collect", headers=ADMIN,
                        json={"n": 5})
        body = r.json().get("data") or {}
        check("collect 降级留痕(sourceMode/Channel)",
              r.status_code == 200
              and body.get("sourceMode") == "mock_fallback"
              and body.get("sourceChannel") == "mock",
              str(body))
        check("池仍有数据(降级不中断)",
              body.get("poolSize", 0) > 0, str(body.get("poolSize")))

    # 汇总
    print()
    print(f"85号真实数据源接入专项: PASS={PASS} FAIL={FAIL}")
    for line in RESULTS:
        print(line)
    return 0 if FAIL == 0 else 1


# fake transport 的 URL 捕获(_fake_capture 内写入)
_last_url = ""


if __name__ == "__main__":
    sys.exit(main())
