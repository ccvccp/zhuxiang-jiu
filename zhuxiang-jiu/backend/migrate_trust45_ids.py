"""45号 P7-1·存量 trustId 迁移工具(dry-run 完整版)

用法(容器内, 方案 §二):
    dry-run: Get-Content .\\migrate_trust45_ids.py -Raw |
        ssh root@47.236.61.117 "docker exec -i zhuxiang-backend-1 python -"
    (exec/verify 待 dry-run 报告人工确认后解锁——方案排期 §六)

dry-run 步骤(零写入, 方案 §1.2 ①② + §三):
    1. 全档案三分类: same_value(P6 同值档)/legacy(存量自助→迁移
       对象)/test(残留候选: digest=名称明文 或 score≥1000)
    2. legacy 逐档预检: 实名索引定位 memberId(digest↔idcard_hash
       双向) + 目标冲突(该 memberId 已有档?) + 关联盘点
       (events 计数/47号风险档案)
    3. 测试残留清理候选清单
    4. 迁移预检结论(可迁/冲突/待人工确认)
"""

import asyncio
import hashlib
import json
from datetime import UTC, datetime


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


async def main():
    from repositories.auth_repository import AuthRepository
    from repositories.trust_risk_repository import (
        TrustRisk47Repository,
    )
    from repositories.trust_value_repository import (
        TrustValue45Repository, id_digest,
    )

    repo = TrustValue45Repository()
    auth_repo = AuthRepository()
    risk_repo = TrustRisk47Repository()

    profiles = await repo.list_profiles(limit=5000)
    print(f"=== 45号 P7-1 dry-run ({_now()}) ===")
    print(f"档案总数: {len(profiles)}\n")

    same_value, legacy, test = [], [], []
    for p in profiles:
        tid = p.get("trustId")
        digest = str(p.get("idDigest") or "")
        score = float(p.get("score") or 0)
        # P6 同值档: 占位摘要(member:{id})或其实名摘要(升级后)
        if digest == id_digest(f"member:{tid}"):
            same_value.append(p)
        elif digest == id_digest(str(p.get("name") or "")) \
                or score >= 1000:
            test.append(p)
        else:
            legacy.append(p)

    print(f"[分类] 同值档(P6)={len(same_value)} "
          f"存量自助(迁移对象)={len(legacy)} "
          f"测试残留候选={len(test)}")

    # ---- 存量自助档逐档预检 ----
    print("\n--- 存量自助档预检(方案 §1.2 ①②) ---")
    sha_probe = (id_digest("probe-45") ==
                 hashlib.sha256(b"probe-45").hexdigest())
    print(f"digest 算法一致性(45号 id_digest == sha256): {sha_probe}")

    migrations = []
    for p in legacy:
        tid = p.get("trustId")
        digest = str(p.get("idDigest") or "")
        print(f"\n[迁移档 trustId={tid}] name={p.get('name')} "
              f"grade={p.get('grade')} score={p.get('score')}")
        # ① 身份确认: 实名索引双向定位
        member_id = None
        try:
            member_id = await auth_repo.get_member_by_idcard_hash(
                digest)
        except Exception as exc:  # noqa: BLE101
            print(f"  实名索引查询异常: {exc}")
        if member_id:
            print(f"  身份确认: memberId={member_id} "
                  f"(实名 idcard_hash 匹配 ✓ 自动)")
        else:
            print("  身份确认: 实名索引无匹配 —— 列人工确认项")
        # ② 冲突检测: 目标 memberId 已有档?
        conflict = None
        if member_id:
            target = await repo.get_profile(int(member_id))
            if target is not None:
                conflict = (f"目标 memberId={member_id} 已有档案"
                            f"(trustId={target['trustId']})")
        print(f"  冲突检测: {conflict or '无冲突 ✓'}")
        # 关联盘点
        events = await repo.list_events_by_trust(tid)
        print(f"  关联盘点: events={len(events)}"
              f"(含 repairs 内嵌) ", end="")
        try:
            risk = await risk_repo.get_profile(tid)
            print(f"47号风险档案={'有' if risk else '无'}")
        except Exception:  # noqa: BLE101
            print("47号风险档案=查询跳过")
        verdict = ("可迁(自动确认)" if member_id and not conflict
                   else "待人工" if not member_id else "冲突阻断")
        print(f"  预检结论: {verdict}")
        migrations.append({
            "oldTrustId": tid, "memberId": member_id,
            "conflict": conflict, "events": len(events),
            "verdict": verdict,
            "profileSnapshot": dict(p),
        })

    # ---- 测试残留候选 ----
    print("\n--- 测试残留清理候选(§三, 默认不执行) ---")
    for p in test:
        print(f"  trustId={p.get('trustId')} name={p.get('name')} "
              f"score={p.get('score')} "
              f"(digest=名称明文={str(p.get('idDigest')) == id_digest(str(p.get('name') or ''))})")

    # ---- 汇总 ----
    auto = [m for m in migrations
            if m["verdict"] == "可迁(自动确认)"]
    manual = [m for m in migrations if m["verdict"] == "待人工"]
    blocked = [m for m in migrations if m["verdict"] == "冲突阻断"]
    print(f"\n=== dry-run 汇总 ===")
    print(f"自动可迁: {len(auto)} / 人工确认: {len(manual)} / "
          f"冲突阻断: {len(blocked)} / 清理候选: {len(test)}")
    print("迁移存档预览(idmap payload):")
    print(json.dumps(migrations, ensure_ascii=False, default=str)[:2000])

asyncio.run(main())
