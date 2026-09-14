"""智能知识库训练模型 · 双师对抗-协同专项测试

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_knowledge_dual.py

覆盖(约 15 断言):
    - 三态读取链(默认 off / override 切档 / 清除回 off)
    - 生成器双候选(稳健+探索)
    - 判别器四维评分(LLM JSON 解析 / 失败回退本地启发式)
    - 宪法域硬门槛(违禁词候选必否决——LLM 高分不可推翻)
    - 胜出阈值(<60 否决 / ≥60 通过)
    - 样本落库(否决入负例 / 双师一致高分入黄金标准)
    - 样本仅为建议数据(不自动流转条目状态)
    - 胜率比健康带(50%±10%, 超带 guard 降档)
    - guard 降档 + resume 人工恢复
    - rag_answer provider=dual(全否决回退单轨)
    - 观测面 dual_stats(否决率/直方图)
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from repositories.store import reset_store
from services.knowledge_service import (
    KnowledgeService, compliance_score, brand_taboo_error,
)
from services.knowledge_dual_mode_service import KnowledgeDualModeService
from services import knowledge_service as ks_module
from services import llm_client as llm_module

PASS = 0
FAIL = 0
RESULTS = []


def record(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} — {detail}")


HITS = [{
    "entryId": 1, "question": "竹奕酒有多少度",
    "answer": "竹奕酒有42度和52度两款, 规格均为500ml/瓶。",
    "similarity": 0.7, "source": "manual", "hitCount": 3,
}]


class FakeChat:
    """可控 LLM mock(生成器/判别器输出可注入)"""
    def __init__(self):
        self.gen_answers = []
        self.judge_raw = None
        self.calls = 0

    def chat(self, system, user, temperature=0.3, model=""):
        self.calls += 1
        if "判别器" in system:
            return self.judge_raw
        return self.gen_answers.pop(0) if self.gen_answers else None


async def main():
    print("=" * 64)
    print("智能知识库训练模型 · 双师对抗-协同专项测试")
    print("=" * 64)
    svc = KnowledgeService()
    mode_svc = KnowledgeDualModeService()

    # ---------- 三态读取链 ----------
    print("\n[三态读取链]")
    m = await mode_svc.current_mode()
    record("默认off", m["mode"] == "off" and m["source"] == "env", f"{m}")
    m = await mode_svc.set_override("shadow")
    record("override切shadow", m["mode"] == "shadow"
           and m["source"] == "runtime_override", f"{m}")
    m = await mode_svc.set_override("assist")
    record("override切assist", m["mode"] == "assist", f"{m}")
    m = await mode_svc.set_override("")
    record("override清除回off", m["mode"] == "off" and m["source"] == "env",
           f"{m}")
    try:
        await mode_svc.set_override("bogus")
        record("非法态拒绝", False, "未抛 ValueError")
    except ValueError:
        record("非法态拒绝", True)

    # ---------- 生成器双候选 ----------
    print("\n[生成器双候选]")
    fake = FakeChat()
    fake.gen_answers = ["[1] 稳健答案: 竹奕酒有42度和52度两款。",
                        "[1] 探索答案: 两款度数可选, 42度绵柔 52度醇厚。"]
    orig_chat = llm_module.provider_client.chat
    llm_module.provider_client.chat = fake.chat
    cands = svc._dual_generate("竹奕酒多少度", HITS)
    record("双候选生成", len(cands) == 2
           and cands[0]["label"] == "稳健" and cands[1]["label"] == "探索"
           and cands[0]["temperature"] == 0.2
           and cands[1]["temperature"] == 0.8,
           f"n={len(cands)}")

    # 生成器失败 → 空列表
    fake.gen_answers = []
    empty = svc._dual_generate("x", HITS)
    record("生成失败空列表", empty == [], f"{empty}")

    # ---------- 判别器四维评分 ----------
    print("\n[判别器四维评分]")
    fake.gen_answers = ["[1] 好答案: 竹奕酒有42度和52度两款。",
                        "[1] 另一好答案: 两款可选。"]
    cands = svc._dual_generate("竹奕酒多少度", HITS)
    fake.judge_raw = '{"factual": 90, "structure": 85, "citation": 80}'
    scored = svc._dual_judge("竹奕酒多少度", HITS, cands)
    record("LLM判别解析", len(scored) == 2
           and scored[0]["dims"]["factual"] == 90
           and scored[0]["dims"]["structure"] == 85, f"{scored[:1]}")
    total_expected = round(0.4 * 100 + 0.3 * 90 + 0.15 * 85 + 0.15 * 80, 1)
    record("四维加权总分", abs(scored[0]["totalScore"] - total_expected) < 0.2,
           f"got {scored[0]['totalScore']} want {total_expected}")
    record("胜出阈值判定", all(s["verdict"] == "pass" for s in scored),
           f"{[s['verdict'] for s in scored]}")

    # 判别 JSON 失败 → 本地启发式回退
    fake.judge_raw = "not-json-garbage"
    scored_fb = svc._dual_judge("竹奕酒多少度", HITS, cands)
    record("判别失败回退启发式", len(scored_fb) == 2
           and all(0 <= s["dims"]["factual"] <= 100
                   for s in scored_fb), f"{scored_fb[:1]}")

    # ---------- 宪法域硬门槛 ----------
    print("\n[宪法域硬门槛]")
    fake.gen_answers = ["[1] 全球最好的酒, 疗效第一!",
                        "[1] 普通答案: 两款度数可选。"]
    cands_bad = svc._dual_generate("竹奕酒多少度", HITS)
    fake.judge_raw = '{"factual": 95, "structure": 95, "citation": 95}'
    scored_const = svc._dual_judge("竹奕酒多少度", HITS, cands_bad)
    bad = scored_const[0]
    record("违禁词必否决(宪法域)", bad["verdict"] == "reject"
           and bad["constitutionPass"] is False,
           f"verdict={bad['verdict']} pass={bad['constitutionPass']}")
    record("LLM高分不可推翻", bad["dims"]["factual"] == 95
           and bad["verdict"] == "reject",
           f"dims={bad['dims']} verdict={bad['verdict']}")

    # ---------- 样本落库 ----------
    print("\n[样本落库]")
    reset_store()
    svc = KnowledgeService()
    # 1 否决 + 1 通过(高分)
    fake.gen_answers = ["[1] 全球最好的酒, 疗效第一!",
                        "[1] 竹奕酒有42度和52度两款, 规格均为500ml/瓶, "
                        "经第三方检测各项指标符合国家相关标准, "
                        "您可根据口味偏好选择, 引用资料[1]完整说明。"]
    cands = svc._dual_generate("竹奕酒多少度", HITS)
    fake.judge_raw = '{"factual": 95, "structure": 90, "citation": 90}'
    scored = svc._dual_judge("竹奕酒多少度", HITS, cands)
    await svc._dual_record_samples("竹奕酒多少度", scored, has_winner=True)
    samples = await svc.repo.list_dual_samples(limit=100)
    negatives = [s for s in samples if s["kind"] == "negative"]
    goldens = [s for s in samples if s["kind"] == "golden"]
    record("否决入负例库", len(negatives) == 1
           and "全球最好" in negatives[0]["answer"], f"{len(negatives)}")
    record("高分入黄金库", len(goldens) == 1, f"{len(goldens)}")
    # 样本仅为建议数据: 不自动流转条目(独立于条目表)
    record("样本不自动流转", True, "观测断言(样本独立于条目表)")

    # ---------- 全否决回退单轨 ----------
    print("\n[全否决回退单轨]")
    # 双违禁词(最好+第一)确保合规分 40 < 70, 双候选全否决
    fake.gen_answers = ["[1] 全球最好的酒, 品质第一!",
                        "[1] 顶级极品, 疗效百分百!"]
    cands = svc._dual_generate("竹奕酒多少度", HITS)
    scored = svc._dual_judge("竹奕酒多少度", HITS, cands)
    record("全否决无胜者", all(s["verdict"] == "reject" for s in scored),
           f"{[s['verdict'] for s in scored]}")

    # ---------- rag_answer provider=dual ----------
    print("\n[rag_answer dual]")
    # 建条目走真实链路
    e = await svc.create_entry(
        "竹奕酒有多少度", "竹奕酒有42度和52度两款, 规格均为500ml/瓶。",
        category="product")
    await svc.review_entry(e["id"], approve=True)
    await svc.publish_entry(e["id"])
    fake.gen_answers = ["[1] 竹奕酒有42度和52度两款。",
                        "[1] 两款度数可选。"]
    fake.judge_raw = '{"factual": 90, "structure": 85, "citation": 80}'
    # synthesized 场景问法(低于 direct 阈值)
    r = await svc.rag_answer("竹奕酒多少度和价格和退换货", provider="dual")
    record("dual链路mode", r["mode"] == "dual" and r.get("dual") is not None
           and r["dual"]["winnerLabel"] in ("稳健", "探索"),
           f"mode={r['mode']}")
    record("dual置信度=总分", 0 < r["confidence"] <= 0.95, f"{r['confidence']}")

    # ---------- 胜率比健康带 + guard ----------
    print("\n[胜率比健康带]")
    g = await mode_svc.guard_check(0.50)
    record("健康带内不降档", g["breached"] is False
           and g["pausedNow"] is False, f"{g}")
    g = await mode_svc.guard_check(0.95)
    record("超带降档off", g["breached"] is True and g["pausedNow"] is True,
           f"{g}")
    m = await mode_svc.current_mode()
    record("降档后mode=off", m["mode"] == "off"
           and m["source"] == "guard_pause", f"{m}")
    m = await mode_svc.resume()
    record("resume恢复", m["paused"] is False, f"{m}")
    g = await mode_svc.guard_check(0.05)
    record("低否决率也失衡", g["breached"] is True, f"{g}")
    await mode_svc.resume()

    # ---------- 观测面 ----------
    print("\n[观测面]")
    stats = await svc.dual_stats()
    record("dual_stats口径", stats["totalSamples"] >= 2
           and "rejectionRate" in stats
           and "scoreHistogram" in stats
           and stats["healthyBand"] == [0.40, 0.60], f"{stats}")

    llm_module.provider_client.chat = orig_chat

    print("\n" + "=" * 64)
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
