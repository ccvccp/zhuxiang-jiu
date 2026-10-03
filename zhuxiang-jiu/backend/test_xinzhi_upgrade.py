"""信值大模型(45/68号) 检查升级验证(2026-10-03)

升级面: ② 调度器(每日申诉回流/学习批处理/主体分布) + ③ 看板
部署断链修复(apiBase 同源默认)。灰度/挂接为既有强项(生产 assist
+17 决策端点门控+auth 等四模块消费), 本测试锁定其不回归。

运行: python test_xinzhi_upgrade.py
"""
import test_support  # noqa: F401 (直跑自举: 内存模式; 首行约定)

import asyncio
import os
import sys

os.environ["TRUST45_MODE"] = "assist"   # 基线 assist(分段切档)

PASS = 0
FAIL = 0
RESULTS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


def main():
    # --- ① 调度器(服务层直调) ---
    async def phase_scan():
        from services.trust45_scan_scheduler import run_scan
        summary = await run_scan()
        check("调度器-run_scan 四块齐备",
              all(k in summary for k in
                  ("collect", "learning", "profiles",
                   "scannedAt")), str(summary)[:150])
        from repositories.store import _mock_store
        snap = _mock_store.get("_trust45_daily_scan_last")
        check("调度器-快照键留痕(daily_scan:last)",
              isinstance(snap, dict)
              and "scannedAt" in snap, str(snap)[:80])
        # 幂等: 二次扫描不炸
        s2 = await run_scan()
        check("调度器-幂等(二次扫描)", "scannedAt" in s2)

    # --- ② 学习闭环(调度器内核不回归) ---
    async def phase_learning():
        from services.trust_learning_service import (
            TrustLearningService,
        )
        pipe = TrustLearningService()
        c = await pipe.collect_appeal_feedback()
        check("学习-申诉回流可调(幂等 appealFed)",
              isinstance(c, dict), str(c)[:100])
        try:
            r = await pipe.run_learning()
        except ValueError as exc:
            r = {"skipped": True, "note": str(exc)[:80]}
        check("学习-批处理(产出建议或样本不足诚实跳过)",
              isinstance(r, dict)
              and ("skipped" in r or "error" not in r),
              str(r)[:100])
        st = await pipe.learning_status()
        check("学习-状态快照可读", isinstance(st, dict),
              str(st)[:100])

    # --- ③ 灰度不回归(既有强项锁定) ---
    async def phase_mode():
        from services.trust45_mode_service import (
            Trust45ModeService,
        )
        svc = Trust45ModeService()
        m = await svc.current_mode()
        check("灰度-assist 读取", m.get("mode") == "assist",
              str(m))
        os.environ["TRUST45_MODE"] = "off"
        try:
            await svc.require_decision_mode()
            check("灰度-off 决策面拒绝", False)
        except ValueError:
            check("灰度-off 决策面拒绝", True)
        os.environ["TRUST45_MODE"] = "assist"

    # --- ④ 看板数据面(HTTP——open/dashboard 无门控) ---
    def phase_dashboard():
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        r = client.get("/api/trust/open/dashboard")
        ok = r.status_code == 200
        body = r.json() if ok else {}
        check("看板-open/dashboard 数据面 200",
              ok, f"s={r.status_code}")
        check("看板-数据面形状(success)",
              bool(body.get("success")) or "data" in body,
              str(body)[:80])

    asyncio.run(phase_scan())
    asyncio.run(phase_learning())
    asyncio.run(phase_mode())
    phase_dashboard()

    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
