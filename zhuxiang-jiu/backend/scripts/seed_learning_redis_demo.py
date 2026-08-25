"""AI 自学习层 Redis 直写种子(order_risk 多版本演进演示数据)

用途:
    - 不经过后端 HTTP, 直接向 Redis 写入 v1→v4 权重版本演进数据
    - 供前端详情页(ai-learning-detail.html)验证多版本演进曲线显示效果
    - 与 seed_learning_demo.py(HTTP 走后端)互补: 本脚本无需后端运行,
      但查看数据时后端必须以 STORE_MODE=redis 启动

写入内容(键格式对齐 repositories/ai_learning_repository.py):
    zhuxiang:ai_learning:profile:order_risk    权重档案(冠军 v4)
    zhuxiang:ai_learning:history:order_risk    退役历史 [v3, v2, v1]
    zhuxiang:ai_learning:feedback:order_risk   15 条已学习反馈(v1/v2/v3 各 5 条)
    zhuxiang:ai_learning:feedback:seq          反馈序列号(=15, 防 ID 冲突)
    zhuxiang:ai_learning:drift:order_risk      漂移统计(15 条样本, medium)

数据设计(让页面各区块都有可看的演示效果):
    - 权重逐版本演进: credit 上升 / amount 下降 / cancel_rate 上升
    - 版本正确率爬坡: v1 60% → v2 80% → v3 100%(报表曲线上升)
    - 反馈 source=auto: 总览页「24h 自动反馈」卡片有数据
    - 漂移等级 medium: 漂移灯显示橙色「关注」

运行(需能连上 Redis, 无需后端):
    py scripts/seed_learning_redis_demo.py
    $env:REDIS_URL = "redis://127.0.0.1:6379/0"; py scripts/seed_learning_redis_demo.py

依赖: redis>=5.0.0(与后端 requirements 一致)
退出码: 0 = 成功 / 1 = 连接失败
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    import redis
except ImportError:
    sys.exit("[FAIL] 缺少 redis 包(后端 requirements.txt 已含, "
             "请在装有后端依赖的解释器下运行)")

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
SCORER = "order_risk"
PREFIX = "zhuxiang:ai_learning"

# order_risk 8 因子默认权重(与 OrderRiskScorer.WEIGHTS 对齐)
BASE_WEIGHTS = {
    "credit": 0.20, "register_age": 0.15, "amount": 0.15,
    "quantity": 0.10, "cancel_rate": 0.15, "address": 0.10,
    "remark": 0.10, "time_pattern": 0.05,
}

# 各版本相对默认的乘数(演示「credit↑ / amount↓ / cancel_rate↑」演进)
VERSION_MULTIPLIERS = {
    "v1": {},
    "v2": {"credit": 1.25, "amount": 0.75},
    "v3": {"credit": 1.50, "amount": 0.60, "cancel_rate": 1.15},
    "v4": {"credit": 1.75, "amount": 0.50, "cancel_rate": 1.30, "remark": 0.85},
}

# 每版本的评估指标(演示正确率爬坡: 60% → 80% → 100%)
VERSION_STATS = {
    "v1": {},
    "v2": {"rewardAlignment": 0.612, "accuracy": 0.60, "samples": 5},
    "v3": {"rewardAlignment": 0.655, "accuracy": 0.80, "samples": 10},
    "v4": {"rewardAlignment": 0.701, "accuracy": 1.00, "samples": 15},
}


def ts(delta_hours: float = 0.0) -> str:
    """UTC ISO 时间戳(对齐 core.helpers.ts), 可前移若干小时"""
    t = datetime.now(timezone.utc) - timedelta(hours=delta_hours)
    return t.isoformat()


def version_weights(version: str) -> dict:
    """按乘数生成归一化权重(和恒为 1.0, 保留 4 位小数)"""
    raw = {k: v * VERSION_MULTIPLIERS[version].get(k, 1.0)
           for k, v in BASE_WEIGHTS.items()}
    total = sum(raw.values())
    normalized = {k: round(v / total, 4) for k, v in raw.items()}
    # 修正舍入误差, 保证总和精确 1.0
    diff = round(1.0 - sum(normalized.values()), 4)
    top = max(normalized, key=normalized.get)
    normalized[top] = round(normalized[top] + diff, 4)
    return normalized


def version_record(version: str, parent: str, hours_ago: float) -> dict:
    source = "default" if version == "v1" else "learning"
    note = ("初始默认权重" if version == "v1"
            else "learned from 5 feedback (demo seed)")
    return {
        "version": version,
        "weights": version_weights(version),
        "source": source,
        "parentVersion": parent,
        "stats": VERSION_STATS[version],
        "note": note,
        "createdAt": ts(hours_ago),
    }


def feedback_record(fid: int, version: str, correct: bool,
                    hours_ago: float) -> dict:
    """一条已学习反馈(source=auto 演示 v7.6 自动反馈闭环)"""
    factors = []
    for idx, (name, weight) in enumerate(BASE_WEIGHTS.items()):
        score = 20 + ((int(version[1:]) * 13 + fid * 7 + idx * 11) % 66)
        factors.append({
            "name": name, "score": float(score), "weight": weight,
            "contribution": round(score * weight, 1),
        })
    return {
        "scorerId": SCORER,
        "weightVersion": version,
        "scoreAtDecision": round(sum(f["score"] for f in factors)
                                 / len(factors), 1),
        "actualAction": "pass",
        "expectedAction": "pass" if correct else "block",
        "correct": correct,
        "factors": factors,
        "note": f"redis demo seed {version}#{fid}",
        "source": "auto",
        "status": "learned",
        "createdAt": ts(hours_ago),
        "feedbackId": fid,
        "learnedAt": ts(hours_ago - 0.5),
    }


def drift_stats() -> dict:
    """漂移统计: 因子 EMA 相对基线偏移(medium 演示橙色关注灯)"""
    baseline = {"credit": 42, "register_age": 35, "amount": 50, "quantity": 20,
                "cancel_rate": 30, "address": 60, "remark": 15, "time_pattern": 25}
    ema = {k: round(v * (1.05 if k in ("credit", "cancel_rate") else 0.97), 2)
           for k, v in baseline.items()}
    return {
        "count": 15, "baselineScore": 34.6, "emaScore": 36.4,
        "baselineFactors": baseline, "emaFactors": ema,
        "driftScore": 0.0497, "driftLevel": "medium", "lastFeedbackAt": ts(0.2),
    }


def main() -> None:
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        client.ping()
    except redis.ConnectionError as exc:
        sys.exit(f"[FAIL] Redis 连接失败({REDIS_URL}): {exc}")

    keys = {
        "profile": f"{PREFIX}:profile:{SCORER}",
        "history": f"{PREFIX}:history:{SCORER}",
        "feedback": f"{PREFIX}:feedback:{SCORER}",
        "seq": f"{PREFIX}:feedback:seq",
        "drift": f"{PREFIX}:drift:{SCORER}",
    }

    print("=" * 64)
    print(f"AI 自学习 Redis 直写种子: scorer={SCORER}  Redis={REDIS_URL}")
    print("=" * 64)

    # 1. 清旧(幂等)
    deleted = client.delete(*keys.values())
    print(f"[OK] 清理旧键: {deleted} 个")

    # 2. 版本演进: v1(3h前) → v2(2h前) → v3(1h前) → v4(当前, 冠军)
    v1 = version_record("v1", "-", 3.0)
    v2 = version_record("v2", "v1", 2.0)
    v3 = version_record("v3", "v2", 1.0)
    v4 = version_record("v4", "v3", 0.0)

    client.set(keys["profile"], json.dumps(
        {"champion": v4, "challenger": None}, ensure_ascii=False))
    print("[OK] 档案写入: 冠军 v4(source=learning), 无挑战者")

    # 历史列表: 新→旧 [v3, v2, v1](对齐 add_history 的 lpush 语义)
    client.rpush(keys["history"],
                 json.dumps(v3, ensure_ascii=False),
                 json.dumps(v2, ensure_ascii=False),
                 json.dumps(v1, ensure_ascii=False))
    print("[OK] 历史写入: v3, v2, v1(退役, 审计用)")

    # 3. 反馈: v1×5(3对) → v2×5(4对) → v3×5(5对), 全部 learned + auto
    correct_plan = {  # 正确率爬坡 60% → 80% → 100%
        "v1": [True, True, True, False, False],
        "v2": [True, True, True, True, False],
        "v3": [True, True, True, True, True],
    }
    fid, hours = 0, 3.0
    for version in ("v1", "v2", "v3"):
        for correct in correct_plan[version]:
            fid += 1
            client.rpush(keys["feedback"], json.dumps(
                feedback_record(fid, version, correct, hours), ensure_ascii=False))
            hours -= 0.15
    client.set(keys["seq"], fid)
    print(f"[OK] 反馈写入: {fid} 条(learned/source=auto), seq={fid}")

    # 4. 漂移统计
    client.set(keys["drift"], json.dumps(drift_stats(), ensure_ascii=False))
    print("[OK] 漂移写入: count=15, level=medium")

    # 5. 摘要
    print("-" * 64)
    for v in ("v1", "v2", "v3", "v4"):
        w = version_weights(v)
        moved = ", ".join(
            f"{k}={w[k]:.3f}" for k in ("credit", "amount", "cancel_rate"))
        print(f"  {v}: {moved}")
    print("-" * 64)
    print("[DONE] 查看: STORE_MODE=redis 启动后端, 打开")
    print(f"        ai-learning-detail.html?scorerId={SCORER}")
    print("        预期曲线: v1→v4 四点, 冠军▲在 v4")


if __name__ == "__main__":
    main()
