"""AI 自学习层演示数据种子脚本(通过 HTTP 直连运行中的后端)

用途:
    - 驾驶舱详情页「权重演进曲线」演示: 构造多版本演进数据
    - 默认场景只有 v1 一个点(从未学习); 本脚本通过
      「提交反馈 → 触发学习」多轮循环, 让曲线出现 v1→v2→v3→v4 演进

做法(以 order_risk 为例, 可用 --scorer 换目标):
    1. PUT config: min_feedback=5(降低触发门槛) + auto_apply=true(学习更优自动晋升)
       → 晋升后旧冠军退役入历史, 曲线点位随轮数增长
    2. 每轮提交 5 条反馈(因子分数逐轮变化, 正确标注为主), 再 POST learn
    3. 结束打印 冠军/挑战者/历史版本 摘要, 供与页面曲线核对

运行(后端需已启动, 默认 http://localhost:8000):
    py scripts/seed_learning_demo.py
    py scripts/seed_learning_demo.py --scorer traffic_antifraud --rounds 4
    $env:AI_DASH_API = "http://127.0.0.1:8000"; py scripts/seed_learning_demo.py

依赖: 仅 Python 标准库(urllib), 无第三方依赖
退出码: 0 = 成功 / 1 = 请求失败或版本未增长
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("AI_DASH_API", "http://localhost:8000").rstrip("/")

# order_risk 8 因子(与 ai_scoring_service.OrderRiskScorer.WEIGHTS 对齐)
ORDER_RISK_FACTORS = [
    "credit", "register_age", "amount", "quantity",
    "cancel_rate", "address", "remark", "time_pattern",
]

HEADERS = {"Content-Type": "application/json", "X-Role": "admin"}


def call(method: str, path: str, body: dict | None = None) -> dict:
    """发请求并返回 JSON, 失败时抛 SystemExit(带错误详情)"""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, headers=HEADERS,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        sys.exit(f"[FAIL] {method} {path} -> HTTP {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        sys.exit(f"[FAIL] {method} {path} -> 连接失败: {exc.reason}"
                 f"(后端是否已在 {API} 启动?)")


def build_feedback(scorer_id: str, factors: list[str], round_no: int,
                   seq: int, correct: bool) -> dict:
    """构造一条反馈: 因子分数随轮次/序号变化, 让曲线有可见起伏

    分数设计: 以 round_no/seq 为种子做线性变化(20~85 区间),
    保证不同轮次的高影响因子不同 → Hedge 更新后权重逐轮移动。
    """
    fb_factors = []
    for idx, name in enumerate(factors):
        score = 20 + ((round_no * 13 + seq * 7 + idx * 11) % 66)
        fb_factors.append({"name": name, "score": score})
    total = sum(f["score"] for f in fb_factors)
    return {
        "scorerId": scorer_id,
        "factors": fb_factors,
        "scoreAtDecision": round(min(total / len(factors), 100), 1),
        "actualAction": "pass",
        "correct": correct,
        "note": f"demo seed r{round_no}#{seq}",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 自学习演示数据种子")
    parser.add_argument("--scorer", default="order_risk",
                        help="评分器ID(默认 order_risk)")
    parser.add_argument("--rounds", type=int, default=3,
                        help="学习轮数(默认 3 → 曲线 v1..v4)")
    parser.add_argument("--per-round", type=int, default=5,
                        help="每轮反馈条数(默认 5, 需 >= min_feedback)")
    parser.add_argument("--keep-config", action="store_true",
                        help="保留演示配置(默认结束时恢复 min_feedback=10/auto_apply=false)")
    args = parser.parse_args()

    scorer = args.scorer
    # 取该评分器的默认权重键集(因子清单)
    weights_view = call("GET", f"/api/ai-learning/weights/{scorer}")
    factors = sorted((weights_view.get("defaults") or {}).keys())
    if not factors:
        sys.exit(f"[FAIL] {scorer} 无可学习因子(不在 16 个可学习档案内?)")

    print("=" * 64)
    print(f"AI 自学习演示种子: scorer={scorer} factors={len(factors)} "
          f"rounds={args.rounds}")
    print(f"后端: {API}")
    print("=" * 64)

    # 1. 降低学习门槛 + 开启自动晋升(演示配置)
    call("PUT", f"/api/ai-learning/config/{scorer}",
         {"min_feedback": args.per_round, "auto_apply": True})
    print(f"[OK] 学习配置已调整: min_feedback={args.per_round}, auto_apply=true")

    # 2. 反馈 → 学习 多轮循环
    for rnd in range(1, args.rounds + 1):
        for seq in range(1, args.per_round + 1):
            # 前 4 条标正确, 末条标错误(让部分因子权重下调, 曲线有升有降)
            correct = seq < args.per_round
            fb = build_feedback(scorer, factors, rnd, seq, correct)
            call("POST", "/api/ai-learning/feedback", fb)
        learned = call("POST", f"/api/ai-learning/learn/{scorer}")
        print(f"[OK] 第{rnd}轮学习: {learned.get('newVersion')} "
              f"(源 {learned.get('parentVersion', '?')} → "
              f"{'冠军' if learned.get('promoted') else '挑战者'}, "
              f"样本={learned.get('learnedFrom')})")

    # 3. 汇总
    history = call("GET", f"/api/ai-learning/history/{scorer}")
    versions = [history.get("champion")] + [
        r.get("version") for r in history.get("history") or []]
    print("-" * 64)
    print(f"冠军: {history.get('champion')}  "
          f"挑战者: {history.get('challenger') or '无'}  "
          f"退役版本: {len(history.get('history') or [])} 个")
    print(f"曲线点位(详情页应显示): {', '.join(v for v in versions if v)}")

    # 4. 恢复默认配置(避免影响后续真实学习节奏)
    if not args.keep_config:
        call("PUT", f"/api/ai-learning/config/{scorer}",
             {"min_feedback": 10, "auto_apply": False})
        print("[OK] 学习配置已恢复默认: min_feedback=10, auto_apply=false")

    if len([v for v in versions if v]) < 2:
        print("[FAIL] 版本数未增长, 请检查上方学习轮输出")
        sys.exit(1)
    print("[DONE] 打开详情页查看演进曲线: "
          f"ai-learning-detail.html?scorerId={scorer}")


if __name__ == "__main__":
    main()
