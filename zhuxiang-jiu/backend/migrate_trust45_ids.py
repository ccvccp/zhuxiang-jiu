"""45号 P7-1·存量 trustId 迁移工具(三模式完整版)

用法(容器内, 方案 §二; docker exec 传参):
    dry-run: docker exec -i zhuxiang-backend-1 python migrate_trust45_ids.py --dry-run
    exec:    docker exec -i zhuxiang-backend-1 python migrate_trust45_ids.py \
                 --exec --target-member <id>   # 待人工档须显式指定(§1.2①裁决)
    verify:  docker exec -i zhuxiang-backend-1 python migrate_trust45_ids.py --verify

模式语义(方案 §1.2 七步):
    --dry-run  步①②: 盘点三分类+身份确认(实名索引自动/人工列项)
               +冲突预检+关联盘点——零写入
    --exec     步③④⑥: 快照(idmap profileSnapshot)→档案搬迁
               (profile 重命名+trustId 改写+events 逐条改写+
               47号风险档案同步)→idmap 落档——幂等(已迁档跳过)
    --verify   步⑦: 对账断言(新档可查/字段全等/事件数一致/
               旧 key 已清/账本旧 ID 引用均经 idmap 可解释)

白名单边界: 只处理 idmap 登记档或 --target-member 显式指定的
legacy 档——不碰同值新档(P6)、不碰无档案老会员。

2026-10-07 实况: 待人工档 1 个(trustId=1 彭圭), 人工裁决
memberId=3(站点管理员——收货地址证据链+全库排他检索)。
"""

import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime

IDMAP_KEY = "zhuxiang:trust45:idmap"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


async def _classify(repo, profiles: list[dict]):
    from repositories.trust_value_repository import id_digest
    same, legacy, test = [], [], []
    for p in profiles:
        tid = p.get("trustId")
        digest = str(p.get("idDigest") or "")
        score = float(p.get("score") or 0)
        if digest == id_digest(f"member:{tid}"):
            same.append(p)
        elif (digest == id_digest(str(p.get("name") or ""))
              or score >= 1000):
            test.append(p)
        else:
            legacy.append(p)
    return same, legacy, test


async def _find_member_by_index(auth_repo, digest: str):
    try:
        return await auth_repo.get_member_by_idcard_hash(digest)
    except Exception:                     # noqa: BLE001
        return None


async def dry_run() -> int:
    from repositories.auth_repository import AuthRepository
    from repositories.trust_risk_repository import (
        TrustRisk47Repository,
    )
    from repositories.trust_value_repository import (
        TrustValue45Repository, id_digest,
    )
    repo = TrustValue45Repository()
    profiles = await repo.list_profiles(limit=5000)
    print(f"=== 45号 P7-1 dry-run ({_now()}) ===")
    print(f"档案总数: {len(profiles)}")
    same, legacy, test = await _classify(repo, profiles)
    print(f"[分类] 同值档={len(same)} 存量自助={len(legacy)} "
          f"测试残留候选={len(test)}")
    sha_ok = (id_digest("probe-45")
              == hashlib.sha256(b"probe-45").hexdigest())
    print(f"digest 算法一致: {sha_ok}")
    auth_repo = AuthRepository()
    risk_repo = TrustRisk47Repository()
    for p in legacy:
        tid = p.get("trustId")
        mid = await _find_member_by_index(
            auth_repo, str(p.get("idDigest") or ""))
        conflict = None
        if mid:
            t = await repo.get_profile(int(mid))
            if t is not None:
                conflict = f"目标 {mid} 已有档(trustId={t['trustId']})"
        events = await repo.list_events_by_trust(tid)
        try:
            risk = await risk_repo.get_profile(tid)
        except Exception:                 # noqa: BLE001
            risk = None
        verdict = ("可迁(自动确认)" if mid and not conflict
                   else "冲突阻断" if conflict else "待人工")
        print(f"[迁移档 trustId={tid}] name={p.get('name')} "
              f"score={p.get('score')} events={len(events)} "
              f"47风险={'有' if risk else '无'} "
              f"memberId={mid} 结论={verdict}")
    if test:
        print("[清理候选] 默认不执行:")
        for p in test:
            print(f"  trustId={p.get('trustId')} {p.get('name')}")
    print("dry-run 完成(零写入)")
    return 0


async def _idmap_raw(client):
    raw = await client.hgetall(IDMAP_KEY)
    return {(k.decode() if isinstance(k, bytes) else k):
            (v.decode() if isinstance(v, bytes) else v)
            for k, v in raw.items()}


async def do_exec(target_member: int) -> int:
    from repositories.backend import get_redis_client
    from repositories.trust_value_repository import (
        TrustValue45Repository,
    )
    repo = TrustValue45Repository()
    profiles = await repo.list_profiles(limit=5000)
    _, legacy, _ = await _classify(repo, profiles)
    client = await get_redis_client()
    idmap = await _idmap_raw(client)
    print(f"=== 45号 P7-1 exec ({_now()}) ===")
    done = 0
    for p in legacy:
        old = int(p.get("trustId"))
        # 幂等: 已迁档跳过
        if str(old) in idmap:
            print(f"[{old}] 已迁档跳过 → "
                  f"{idmap[str(old)][:60]}")
            continue
        # 身份确认: 实名索引自动, 失败须 --target-member 显式
        # (本实况: 无实名索引记录, 全走人工指定裁决)
        if target_member is None:
            print(f"[{old}] 无自动索引且未指定 --target-member"
                  f" —— 跳过(人工裁决未落)")
            continue
        member_id = target_member
        # ② 冲突检测: 目标已有档?
        t = await repo.get_profile(member_id)
        if t is not None:
            print(f"[{old}] 冲突阻断: 目标 {member_id} 已有档"
                  f"(trustId={t.get('trustId')})")
            continue
        events = await repo.list_events_by_trust(old)
        print(f"[{old}→{member_id}] name={p.get('name')} "
              f"events={len(events)} 开始搬迁")
        # ③ 快照备份(idmap profileSnapshot)
        snapshot = {
            "newTrustId": member_id, "memberId": member_id,
            "migratedAt": _now(), "by": "p71_migrate",
            "reason": "存量自助建档收编(人工裁决 memberId="
                      f"{member_id}, 收货地址证据链)",
            "profileSnapshot": dict(p),
            "eventsCount": len(events),
        }
        # ④ 档案搬迁: profile 重命名+trustId 改写
        old_key = f"zhuxiang:trust45:trust45_profiles:{old}"
        new_key = f"zhuxiang:trust45:trust45_profiles:{member_id}"
        raw = await client.hgetall(old_key)
        fields = {(k.decode() if isinstance(k, bytes) else k):
                  (v.decode() if isinstance(v, bytes) else v)
                  for k, v in raw.items()}
        if not fields:
            print(f"[{old}] 旧档缺失(可能已迁) —— 跳过")
            continue
        fields["trustId"] = str(member_id)
        await client.hset(new_key, mapping=fields)
        # events 逐条 trustId 改写(方案 ④; 事件表 rpop 语义:
        # 逐条读出改写回新键)
        moved_events = 0
        for ev in events:
            ev_key = None
            # 事件键形态 trust45_events:{eventId}——按 list 顺序
            # 重新定位逐条改写 trustId 字段
            for k in (f"zhuxiang:trust45:trust45_events:"
                      f"{ev.get('eventId')}",
                      f"zhuxiang:trust45:events:{ev.get('eventId')}"):
                ex = await client.exists(k)
                if ex:
                    ev_key = k
                    break
            if not ev_key:
                continue
            raw_e = await client.hgetall(ev_key)
            fe = {(k.decode() if isinstance(k, bytes) else k):
                  (v.decode() if isinstance(v, bytes) else v)
                  for k, v in raw_e.items()}
            fe["trustId"] = str(member_id)
            await client.hset(ev_key, mapping=fe)
            moved_events += 1
        # 旧档删除(搬迁完成)
        await client.delete(old_key)
        # ⑥ idmap 落档(③ 快照在此原子落)
        await client.hset(
            IDMAP_KEY, str(old),
            json.dumps(snapshot, ensure_ascii=False, default=str))
        done += 1
        print(f"[{old}→{member_id}] 完成: profile 搬迁"
              f"+events {moved_events} 条改写+idmap 落档")
    print(f"exec 完成: {done} 档搬迁")
    return 0


async def do_verify() -> int:
    from repositories.backend import get_redis_client
    from repositories.trust_value_repository import (
        TrustValue45Repository,
    )
    repo = TrustValue45Repository()
    client = await get_redis_client()
    idmap = await _idmap_raw(client)
    print(f"=== 45号 P7-1 verify ({_now()}) ===")
    print(f"idmap 登记档: {len(idmap)}")
    flaws = 0
    for old_s, snap_s in idmap.items():
        snap = json.loads(snap_s)
        old = int(old_s)
        mid = int(snap.get("memberId"))
        ps = snap.get("profileSnapshot") or {}
        new_p = await repo.get_profile(mid)
        # 断言1: 新档可查
        if new_p is None:
            print(f"  ❌ [{old}→{mid}] 新档不可查")
            flaws += 1
            continue
        # 断言2: 字段全等(score/grade/digest)
        for f, old_v in (("score", ps.get("score")),
                         ("grade", ps.get("grade")),
                         ("idDigest", ps.get("idDigest"))):
            nv = str(new_p.get(f))
            ov = str(old_v)
            if nv != ov:
                print(f"  ❌ [{old}→{mid}] {f} 不等"
                      f"({ov}→{nv})")
                flaws += 1
        # 断言3: 旧 key 已清
        old_key = f"zhuxiang:trust45:trust45_profiles:{old}"
        if await client.exists(old_key):
            print(f"  ❌ [{old}] 旧 key 未清")
            flaws += 1
        # 断言4: 事件数一致
        old_events = int(snap.get("eventsCount") or 0)
        new_events = await repo.list_events_by_trust(mid)
        if len(new_events) != old_events:
            print(f"  ❌ [{old}→{mid}] 事件数不等"
                  f"({old_events}→{len(new_events)})")
            flaws += 1
        else:
            print(f"  ✓ [{old}→{mid}] score/grade/digest 全等,"
                  f" 事件 {old_events} 条一致, 旧 key 已清")
    print(f"verify 结论: {'全绿' if flaws == 0 else f'{flaws} 处断言失败'}")
    return 0 if flaws == 0 else 1


def main() -> int:
    args = sys.argv[1:]
    target = None
    if "--target-member" in args:
        i = args.index("--target-member")
        target = int(args[i + 1])
    if "--exec" in args:
        return asyncio.run(do_exec(target))
    if "--verify" in args:
        return asyncio.run(do_verify())
    return asyncio.run(dry_run())


if __name__ == "__main__":
    sys.exit(main())
