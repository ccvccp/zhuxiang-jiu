"""AI 案例知识库测试(v7.9 阶段 1: 存储/归档/余弦检索/学习闭环接入, 14 项)

覆盖:
    - 开关与转换(2): AI_KB=off 关闭归档 / 无因子反馈跳过
    - 存储与序列(3): 案例 ID 自增 / list 新→旧 / count 统计
    - 余弦检索(4): 相同向量相似度 1.0 / 正交向量 0 /
      相似度降序 top-k 截断 / 缩放不变性(余弦对向量长度不敏感)
    - 封顶裁剪(1): 超出 KB_MAX_CASES 保留最新
    - 统计接口(1): kb_stats 汇总
    - 学习闭环集成(3): 学习周期自动归档 / 归档案例含因子与终态 /
      空查询向量返回空

在宿主机运行(纯标准库, 沙箱可跑):
    cd D:\\网站架构设计\\zhuxiang-jiu\\backend
    python test_ai_knowledge_base.py
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.setdefault("AUTH_MODE", "compat")
os.environ.pop("AI_KB", None)

from repositories.ai_knowledge_repository import AiKnowledgeRepository
from repositories.ai_learning_repository import AiLearningRepository
from repositories.store import _mock_store
from services.ai_learning_service import (
    run_learning_cycle, submit_feedback,
)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} -- {detail}")


async def _mk_feedback(factors: list, correct=True, source="auto"):
    """构造带因子快照的反馈载荷"""
    return {
        "scorerId": "order_risk",
        "factors": factors,
        "scoreAtDecision": 42.0,
        "actualAction": "pass",
        "expectedAction": "pass",
        "correct": correct,
        "source": source,
        "note": "kb-test",
    }


async def main():
    print("=" * 64)
    print("AI 案例知识库测试(存储 / 归档 / 余弦检索 / 学习闭环)")
    print("=" * 64)
    kb = AiKnowledgeRepository()

    f1 = [{"name": "credit", "score": 60}, {"name": "amount", "score": 30}]
    f2 = [{"name": "credit", "score": 62}, {"name": "amount", "score": 28}]
    f3 = [{"name": "remark", "score": 90}]

    # ========================================================
    # 1. 开关与转换
    # ========================================================
    os.environ["AI_KB"] = "off"
    n = await kb.archive_feedback("order_risk",
                                  [await _mk_feedback(f1)])
    os.environ.pop("AI_KB")
    record("01_kb_off_disables_archive", n == 0
           and await kb.count_cases("order_risk") == 0,
           f"archived={n}, count={await kb.count_cases('order_risk')}")

    n = await kb.archive_feedback("order_risk", [
        await _mk_feedback(f1),                      # 有效
        {"scorerId": "order_risk", "factors": [],    # 无因子 → 跳过
         "actualAction": "pass", "correct": True, "source": "auto"},
    ])
    record("02_feedback_without_factors_skipped",
           n == 1 and await kb.count_cases("order_risk") == 1,
           f"archived={n}, count={await kb.count_cases('order_risk')}")

    # ========================================================
    # 2. 存储与序列
    # ========================================================
    await kb.archive_feedback("order_risk", [await _mk_feedback(f2)])
    await kb.archive_feedback("order_risk", [await _mk_feedback(f3)])
    cases = await kb.list_cases("order_risk")
    ids = [c["caseId"] for c in cases]
    # list 新→旧 → ID 降序; 最早归档的案例 ID=1
    record("03_case_ids_increment", ids == [3, 2, 1]
           and len(set(ids)) == 3,
           f"ids={ids}")
    # 新→旧: 最后归档的 f3 在最前
    record("04_list_newest_first",
           cases[0]["factors"] == {"remark": 90}
           and cases[1]["factors"] == {"credit": 62, "amount": 28}
           and cases[2]["factors"] == {"credit": 60, "amount": 30},
           f"order={[list(c['factors'].keys()) for c in cases]}")
    record("05_count_cases", await kb.count_cases("order_risk") == 3,
           f"count={await kb.count_cases('order_risk')}")

    # 案例字段完整性(归档自反馈的关键语义)
    c = cases[1]
    record("06_case_fields_from_feedback",
           c.get("scoreAtDecision") == 42.0
           and c.get("actualAction") == "pass"
           and c.get("correct") is True
           and c.get("source") == "auto"
           and "archivedAt" in c and c.get("scorerId") == "order_risk",
           f"case={c}")

    # ========================================================
    # 3. 余弦检索
    # ========================================================
    hits = await kb.search_similar("order_risk",
                                   {"credit": 60, "amount": 30}, k=2)
    record("07_identical_vector_similarity_1",
           hits and hits[0]["similarity"] == 1.0
           and hits[0]["caseId"] == 1,
           f"hits={hits[:1]}")

    hits = await kb.search_similar("order_risk", {"remark": 90}, k=3)
    record("08_orthogonal_vector_excluded",
           len(hits) == 1 and hits[0]["similarity"] == 1.0,
           f"hits={hits}")

    # 相似度降序 + top-k 截断(查询靠近 f2, 次近 f1, f3 正交)
    hits = await kb.search_similar("order_risk",
                                   {"credit": 61, "amount": 29}, k=1)
    record("09_topk_desc_order",
           len(hits) == 1 and hits[0]["caseId"] == 2
           and 0.99 < hits[0]["similarity"] < 1.0,
           f"hits={hits}")

    # 缩放不变性: 同方向不同长度 → 相似度仍 1.0
    hits = await kb.search_similar("order_risk",
                                   {"credit": 180, "amount": 90}, k=1)
    record("10_scale_invariance",
           hits and hits[0]["similarity"] == 1.0,
           f"sim={hits and hits[0]['similarity']}")

    # 空查询向量
    record("11_empty_query_returns_empty",
           await kb.search_similar("order_risk", {}) == [],
           "空因子向量应返回空")

    # ========================================================
    # 4. 封顶裁剪(覆盖小上限)
    # ========================================================
    small_kb = AiKnowledgeRepository()
    small_kb.KB_MAX_CASES = 3
    for i in range(6):
        await small_kb.add_case("traffic_antifraud", {
            "factors": {"f": i}, "scoreAtDecision": float(i),
            "actualAction": "pass", "correct": True, "source": "auto"})
    kept = await small_kb.list_cases("traffic_antifraud")
    record("12_cap_prune_keeps_newest",
           len(kept) == 3 and [c["factors"]["f"] for c in kept] == [5, 4, 3],
           f"kept={[c['factors']['f'] for c in kept]}")

    # ========================================================
    # 5. 统计接口
    # ========================================================
    stats = await kb.kb_stats(["order_risk", "traffic_antifraud"])
    # traffic_antifraud 经封顶裁剪保留 3 条(与 kb 实例共享内存 store)
    record("13_kb_stats_aggregates",
           stats.get("success") is True and stats.get("enabled") is True
           and stats.get("scorerCounts", {}).get("order_risk") == 3
           and stats.get("scorerCounts", {}).get("traffic_antifraud") == 3
           and stats.get("totalCases") == 6,
           f"stats={stats.get('scorerCounts')}, total={stats.get('totalCases')}")

    # ========================================================
    # 6. 学习闭环集成: 学习周期自动归档
    # ========================================================
    learn_repo = AiLearningRepository()
    await learn_repo.save_config("points_risk", {"min_feedback": 2})
    for i in range(2):
        await submit_feedback({
            "scorerId": "points_risk",
            "factors": [{"name": "earn_burst", "score": 20 + i * 5,
                         "weight": 0.25, "contribution": 5 + i}],
            "scoreAtDecision": 20.0,
            "actualAction": "low", "expectedAction": "low",
            "correct": True, "source": "auto", "note": "kb-loop",
        })
    result = await run_learning_cycle("points_risk")
    kb_count = await kb.count_cases("points_risk")
    learned_fb = await learn_repo.list_feedback("points_risk",
                                                status="learned", limit=0)
    kb_cases = await kb.list_cases("points_risk")
    record("14_learning_cycle_archives_to_kb",
           result.get("success") and result.get("learnedFrom") == 2
           and kb_count == 2 and len(learned_fb) == 2
           and all(c.get("factors") for c in kb_cases)
           and kb_cases[0].get("factors", {}).get("earn_burst") == 25.0,
           f"learned={result.get('learnedFrom')}, kb={kb_count}, "
           f"cases={[c.get('factors') for c in kb_cases]}")

    # ========================================================
    # 7. 经验回放学习(v7.9 阶段 3)
    # ========================================================
    from services.ai_learning_service import update_learning_config

    cfg = await update_learning_config("order_risk",
                                       {"replay": True,
                                        "replay_sample": 20})
    record("15_replay_config_accepted",
           cfg.get("success") and cfg["config"]["replay"] is True
           and cfg["config"]["replay_sample"] == 20,
           f"cfg={cfg.get('config')}")

    try:
        await update_learning_config("order_risk", {"replay_sample": 0})
        bad_rejected = False
    except ValueError:
        bad_rejected = True
    record("16_replay_config_validated", bad_rejected,
           "replay_sample=0 应被拒绝")

    # 回放关闭(默认): 学习周期 replayedFrom == 0
    await update_learning_config("traffic_antifraud",
                                 {"min_feedback": 1, "replay": False})
    await submit_feedback({
        "scorerId": "traffic_antifraud",
        "factors": [{"name": "burst", "score": 50,
                     "weight": 0.2, "contribution": 10}],
        "scoreAtDecision": 50.0, "actualAction": "review",
        "expectedAction": "review", "correct": True,
        "source": "auto", "note": "kb-replay-off",
    })
    res_off = await run_learning_cycle("traffic_antifraud")
    record("17_replay_off_by_default",
           res_off.get("replayedFrom") == 0,
           f"replayedFrom={res_off.get('replayedFrom')}")

    # 回放开启: 知识库标注案例混入样本 + 权重效果(抑制过拟合)
    # P 反馈: credit=100 且正确(会推高 credit 权重)
    # 回放案例: credit=100 且错误(3 条, 推低 credit 权重)
    async def _submit_credit_pending():
        await submit_feedback({
            "scorerId": "order_risk",
            "factors": [{"name": "credit", "score": 100,
                         "weight": 0.2, "contribution": 100}],
            "scoreAtDecision": 20.0, "actualAction": "pass",
            "expectedAction": "pass", "correct": True,
            "source": "auto", "note": "kb-replay",
        })

    await update_learning_config("order_risk",
                                 {"min_feedback": 1, "replay": False,
                                  "auto_apply": False})
    await _submit_credit_pending()
    res_a = await run_learning_cycle("order_risk")
    d1 = res_a["weightDelta"]["credit"]

    # 种子回放案例(3 条标注错误 + 1 条未标注应跳过)
    for i in range(4):
        await kb.add_case("order_risk", {
            "factors": {"credit": 100.0},
            "scoreAtDecision": 80.0, "action": "pass",
            "actualAction": "block",
            "correct": None if i == 3 else False,
            "source": "auto", "businessKey": f"kb-rp{i}",
        })
    await update_learning_config("order_risk", {"replay": True})
    await _submit_credit_pending()
    res_b = await run_learning_cycle("order_risk")
    d2 = res_b["weightDelta"]["credit"]
    record("18_replay_blends_history_into_update",
           # 计数含历史测试归档(3)+本轮前序学习归档(1)+新种子(3, 未标注跳过)
           res_b.get("replayedFrom") >= 3
           and res_b.get("learnedFrom") == 1
           and d2 < d1,                            # 反向经验抑制权重攀升
           f"replayedFrom={res_b.get('replayedFrom')}, "
           f"d_no_replay={d1}, d_with_replay={d2}")

    # ========================================================
    # 汇总
    # ========================================================
    print("\n".join(RESULTS))
    print("=" * 64)
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    print("=" * 64)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
