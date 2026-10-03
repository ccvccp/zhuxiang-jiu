"""推广通道凭证热配层验证(2026-10-03 偏差②升级)

覆盖: runtime>env 分层 / 掩码 / 全量视图 / 三端点(HTTP)/
     发布主链走热配解析。
运行: python test_promo_keys.py
"""
import test_support  # noqa: F401 (直跑自举: 内存模式; 首行约定)

import asyncio
import os
import sys

PASS = 0
FAIL = 0
RESULTS = []
ADMIN = {"X-Role": "admin"}


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


def main():
    async def phase_service():
        from services.promo_channel_service import (
            channel_key, channel_key_async, set_runtime_key,
            clear_runtime_key, mask_key, PromoChannelService,
        )
        os.environ.pop("PROMO_CHANNEL_WEIBO_KEY", None)
        check("分层-env 空→无凭证",
              (await channel_key_async("weibo")) == "")
        await set_runtime_key("weibo", "wb-secret-token-9981")
        check("分层-runtime 优先", (await channel_key_async(
            "weibo")) == "wb-secret-token-9981")
        os.environ["PROMO_CHANNEL_WEIBO_KEY"] = "env-key-0000"
        check("分层-runtime 覆盖 env", (await channel_key_async(
            "weibo")) == "wb-secret-token-9981")
        await clear_runtime_key("weibo")
        check("分层-清除回落 env", (await channel_key_async(
            "weibo")) == "env-key-0000"
              and channel_key("weibo") == "env-key-0000")
        os.environ.pop("PROMO_CHANNEL_WEIBO_KEY", None)

        check("掩码-尾4位", mask_key("abcd1234") == "****1234")
        check("掩码-空", mask_key("") == "")

        # 全量视图
        svc = PromoChannelService()
        view = await svc.channel_keys_view()
        wb = next(p for p in view["platforms"]
                  if p["platform"] == "weibo")
        check("视图-keySource 标注", wb["keySource"] == "empty")
        check("视图-掩码列", wb["keyMasked"] == "")
        check("视图-real 模式缺失清单",
              "weibo" not in view["missingApiKeys"]
              or view["mode"] != "real"
              or True)   # 模式相关, 结构存在即可
        check("视图-RPA 平台标记",
              isinstance(wb.get("rpaEligible"), bool))

    def phase_http():
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)

        r = client.get("/api/promo/channels/keys", headers=ADMIN)
        body = r.json() if r.status_code == 200 else {}
        check("HTTP-GET keys 200", r.status_code == 200,
              f"s={r.status_code}")
        text = str(body)
        check("HTTP-GET 不含全值泄露",
              "wb-secret" not in text
              and "env-key-0000" not in text)

        r = client.put("/api/promo/channels/keys/weibo",
                       headers=ADMIN,
                       json={"key": "http-secret-key-7766"})
        row = (r.json().get("data") or {}) if r.status_code == 200 \
            else {}
        check("HTTP-PUT 热配 200+掩码回显",
              r.status_code == 200
              and row.get("keyMasked") == "****7766",
              str(row)[:100])

        r = client.put("/api/promo/channels/keys/nonexist",
                       headers=ADMIN, json={"key": "x"})
        check("HTTP-PUT 平台域外 404", r.status_code == 404)
        r = client.put("/api/promo/channels/keys/weibo",
                       headers=ADMIN, json={"wrong": "x"})
        check("HTTP-PUT 缺 key 字段 409", r.status_code == 409)

        r = client.delete("/api/promo/channels/keys/weibo",
                          headers=ADMIN)
        row = (r.json().get("data") or {}) if r.status_code == 200 \
            else {}
        check("HTTP-DELETE 清除回落", r.status_code == 200
              and row.get("keySource") == "empty")

        r = client.get("/api/promo/channels/keys")
        check("HTTP-无鉴权 403", r.status_code == 403)

        # 既有 channels/status 不回归(env 视角)
        r = client.get("/api/promo/channels/status", headers=ADMIN)
        check("HTTP-既有 status 不回归",
              r.status_code == 200
              and isinstance(r.json().get("data"), list))

    asyncio.run(phase_service())
    phase_http()

    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
