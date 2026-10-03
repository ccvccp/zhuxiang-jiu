"""智能推广(36号)运行检查专项(2026-10-03)

检查升级面为"运行态+断链修复"(调度器生产已在跑, 非四件套型),
本测试锁定观测面数据端点与看板断链修复件。

运行: python test_promo_ops_check.py
"""
import test_support  # noqa: F401 (直跑自举: 内存模式; 首行约定)

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
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    # --- ① 观测面数据端点(看板数据源) ---
    eps = [
        ("GET", "/api/promo/radar/hotspots", "雷达-热点库"),
        ("GET", "/api/promo/decisions", "决策-留痕列表"),
        ("GET", "/api/promo/report/overview", "报表-总览"),
        ("GET", "/api/promo/channels/status", "通道-五平台状态"),
        ("GET", "/api/promo/evolution/status", "进化-四引擎状态"),
        ("GET", "/api/promo/audience/profiles", "受众-画像"),
        ("GET", "/api/promo/seo/pushes", "SEO-推送留痕"),
    ]
    for method, path, label in eps:
        r = client.request(method, path, headers=ADMIN)
        check(f"观测面-{label} 200", r.status_code == 200,
              f"s={r.status_code} {r.text[:60]}")

    # --- ② 无鉴权 403(47 admin 端点抽样) ---
    r = client.get("/api/promo/report/overview")
    check("守卫-无鉴权 403", r.status_code == 403,
          f"s={r.status_code}")

    # --- ③ 看板断链修复件(本地静态断言) ---
    base = os.path.dirname(os.path.abspath(__file__))
    for jsf in ("../js/promo-radar.js", "../js/promo-studio.js"):
        p = os.path.normpath(os.path.join(base, jsf))
        try:
            txt = open(p, encoding="utf-8").read()
            check(f"看板js-{os.path.basename(jsf)} 无 localhost 硬默认",
                  "|| 'http://localhost:8000'" not in txt)
        except Exception as e:
            check(f"看板js-{os.path.basename(jsf)} 可读", False, e)

    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
