#!/usr/bin/env python3
"""保证金子系统运维工具(容器内运行, service 层直达——免登录 token)

用法:
    docker exec zhuxiang-backend-1 python /app/margin_ops.py <命令> [参数]

命令:
    status <store_code|margin_no>    查保证金(含实时年任务进度; 兼容店编号/保证金编号)
    list [locked|settled]          全量保证金清单(可按状态筛, 默认全部)
    due [N]                        到期待结算扫描(dry-run); due N=剩余 N 天内到期预警清单
    settle <store_code> <reason>   手动结算(reason: expired|cancelled|rejected, 幂等)
    tx <user_id> [条数]            会员保证金钱包流水(双 type, 默认 10 条)
    stats                          保证金统计概览(状态分布/金额汇总/待结算)
    remind                         手动触发一轮到期提醒(30/7/1 天档位站内信)
    audit                          保证金对账恒等式(内部+钱包流水双口径)

注意:
    - settle 与调度器并发安全(锁内双重检查, 重复调用幂等不双退)
    - 全部命令只读除 settle; settle 走 svc.settle_margin 含锁+流水
"""
import asyncio
import json
import sys
from datetime import date as _date


def _iso(s: str) -> _date:
    """YYYY-MM-DD → date"""
    return _date.fromisoformat(s[:10])


def _fmt_margin(m: dict, verbose: bool = False) -> str:
    """单条保证金一行摘要(或全量 JSON)"""
    if verbose:
        return json.dumps(m, ensure_ascii=False, indent=2)
    status = m.get("status", "?")
    reason = m.get("settleReason") or "-"
    refund = m.get("refundAmount")
    refund_s = f"退{refund}" if refund is not None else ""
    rate = m.get("completionRate")
    rate_s = f"率{rate}" if rate is not None else ""
    return (f"{m.get('marginNo')} | {m.get('storeCode')} | uid={m.get('userId')}"
            f" | {status}" + (f"/{reason} {refund_s} {rate_s}" if status == "settled"
                             else f" | {m.get('startDate') or '未开业'}"
                                  f" ~ {m.get('endDate') or '-'}"))


async def main() -> int:
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "help"

    if cmd in ("help", "-h", "--help"):
        print(__doc__)
        return 0

    from core.helpers import ts
    from repositories.citystore_repository import (
        CityStoreRepository, MARGIN_STATUS_LOCKED, MARGIN_STATUS_SETTLED)
    from services.citystore_service import CityStoreService
    from repositories.wallet_repository import WalletRepository

    repo = CityStoreRepository()
    svc = CityStoreService()
    wallet = WalletRepository()
    today = ts()[:10]

    # ---------- status ----------
    if cmd == "status":
        if len(argv) < 2:
            print("用法: status <store_code|margin_no>")
            return 1
        code = argv[1]
        # 兼容保证金编号(CD 开头——转店编号; 店编号 CS 开头直查)
        m = None
        if code[:2].upper() == "CD":
            m = await repo.get_margin(code)
            sc = (m or {}).get("storeCode", "")
        else:
            sc = code
            m = await repo.get_margin_by_store(sc)
        if m is None:
            print(f"[无记录] {code} 无保证金(存量市级店无保证金)")
            return 0
        # 实时进度(复用 service 逻辑——locked 且有起止)
        result = await svc.get_margin(sc)
        print(_fmt_margin(result, verbose=True))
        return 0

    # ---------- list ----------
    if cmd == "list":
        flt = argv[1] if len(argv) > 1 else None
        if flt and flt not in (MARGIN_STATUS_LOCKED, MARGIN_STATUS_SETTLED):
            print("状态须为 locked|settled(或缺省全部)")
            return 1
        margins = await repo.list_all_margins(flt)
        print(f"[保证金清单] 共 {len(margins)} 条"
              + (f"({flt})" if flt else "") + ":")
        for m in margins:
            print("  " + _fmt_margin(m))
        return 0

    # ---------- due [N] ----------
    if cmd == "due":
        # due N: 剩余 N 天内到期的 locked 预警清单(运营视角)
        if len(argv) > 1 and argv[1].isdigit():
            n = int(argv[1])
            locked = await repo.list_all_margins(MARGIN_STATUS_LOCKED)
            upcoming = []
            for m in locked:
                if not m.get("endDate"):
                    continue
                days_left = (_iso(m["endDate"]) - _iso(today)).days
                if 0 <= days_left <= n:
                    upcoming.append((m, days_left))
            print(f"[到期预警] {today} 剩余 ≤{n} 天到期的 locked 共 "
                  f"{len(upcoming)} 条:")
            for m, dl in upcoming:
                print(f"  {_fmt_margin(m)} | 剩 {dl} 天"
                      f" | 已提醒档位 {m.get('remindedSteps') or '-'}")
            return 0
        due = await repo.list_due_margins(today)
        print(f"[到期待结算] {today} 共 {len(due)} 条:")
        for m in due:
            print("  " + _fmt_margin(m))
        if due:
            print("(调度器每小时自动结算; 手动补结算: "
                  "settle <store_code> expired)")
        return 0

    # ---------- settle ----------
    if cmd == "settle":
        if len(argv) < 3:
            print("用法: settle <store_code> <expired|cancelled|rejected>")
            return 1
        sc, reason = argv[1], argv[2]
        result = await svc.settle_margin(sc, reason)
        if result is None:
            print(f"[无记录] {sc} 无保证金")
            return 1
        print("[结算完成]")
        print(_fmt_margin(result, verbose=True))
        return 0

    # ---------- tx ----------
    if cmd == "tx":
        if len(argv) < 2:
            print("用法: tx <user_id> [条数]")
            return 1
        uid = int(argv[1])
        limit = int(argv[2]) if len(argv) > 2 else 10
        locks = await wallet.list_transactions(
            uid, tx_type="citystore_margin_lock", limit=limit)
        settles = await wallet.list_transactions(
            uid, tx_type="citystore_margin_settle", limit=limit)
        flows = sorted(locks + settles,
                       key=lambda t: t.get("createdAt", ""), reverse=True)
        print(f"[会员 {uid} 保证金流水] 最近 {len(flows)} 条:")
        for t in flows:
            print(f"  {t.get('txNo')} | {t.get('type')} | "
                  f"{t.get('direction')} ¥{t.get('amount')} | "
                  f"余额后 {t.get('balanceAfter')} | "
                  f"{t.get('description')}")
        # 汇总
        lock_sum = sum(float(t.get("amount", 0)) for t in locks)
        settle_sum = sum(float(t.get("amount", 0)) for t in settles)
        print(f"  汇总: 锁定 {len(locks)} 笔 -¥{lock_sum} | "
              f"结算 {len(settles)} 笔 +¥{settle_sum}")
        return 0

    # ---------- stats ----------
    if cmd == "stats":
        all_m = await repo.list_all_margins()
        locked = [m for m in all_m if m.get("status") == MARGIN_STATUS_LOCKED]
        settled = [m for m in all_m if m.get("status") == MARGIN_STATUS_SETTLED]
        due = [m for m in locked if m.get("endDate") and m["endDate"] <= today]
        lock_amt = sum(float(m.get("amount", 0)) for m in locked)
        refund_amt = sum(float(m.get("refundAmount") or 0) for m in settled)
        deduct_amt = sum(float(m.get("deductedAmount") or 0) for m in settled)
        print("保证金统计概览")
        print(f"  总记录: {len(all_m)} | locked {len(locked)}"
              f" | settled {len(settled)}")
        print(f"  在锁金额: ¥{lock_amt} ({len(locked)} 条)")
        print(f"  累计退还: ¥{refund_amt} | 累计扣除: ¥{deduct_amt}")
        print(f"  到期待结算({today}): {len(due)} 条")
        for m in due:
            print("    " + _fmt_margin(m))
        return 0

    # ---------- remind ----------
    if cmd == "remind":
        result = await svc.run_margin_reminder_round()
        print(f"[到期提醒轮] 扫描 {result['scanned']} 条 locked, "
              f"发送 {len(result['sent'])} 条, 跳过 {result['skipped']}, "
              f"失败 {len(result['failed'])}")
        for s in result["sent"]:
            print(f"  {s['marginNo']} | {s['storeCode']} | "
                  f"{s['step']} 天档(剩余 {s['daysLeft']} 天)")
        for f in result["failed"]:
            print(f"  [失败] {f['storeCode']}: {f['error']}")
        return 0

    # ---------- audit ----------
    if cmd == "audit":
        result = await svc.margin_reconciliation()
        i, w = result["internal"], result["walletTx"]
        print("[对账恒等式]")
        print(f"  内部: 总锁定 ¥{i['totalAmount']} ≡ 在锁 ¥{i['inLockAmount']}"
              f" + 已退 ¥{i['refundedAmount']} + 已扣 ¥{i['deductedAmount']}"
              f"  {'✓ 平衡' if i['ok'] else '✗ 不平衡'}")
        print(f"  钱包: 锁定流水 ¥{w['lockTxSum']} ≡ 总锁定"
              f" ¥{i['totalAmount']} | 结算流水 ¥{w['settleTxSum']}"
              f" ≡ 已退 ¥{i['refundedAmount']}"
              f"  {'✓ 平衡' if w['ok'] else '✗ 不平衡'}")
        if not result["ok"]:
            print("  [告警] 恒等式失配——核查 margin 记录与钱包流水")
            return 1
        print("  全部平衡 ✓")
        return 0

    print(f"未知命令: {cmd} (help 查看用法)")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
