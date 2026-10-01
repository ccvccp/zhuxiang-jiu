"""48号·概率型唤醒评分 P1 专项单测

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python -m pytest test_xiaozhu_wake_p1.py -q

覆盖(P1 方案 §一):
    T1 分级评分: 精确 1.0/强近似 0.9/同韵 0.8/弱近似 0.7/
       未注册 u 韵 0.55/无关 0.0
    T2 句中衰减: 前 1/3 ×0.75 / 中后 ×0.65
    T3 off 零差异: WAKE_SCORE_MODE=off 下 v2 判定与旧
       detect_wake 全等(七近似音+未注册音+无关句)
    T4 on 态阈值: 表内全过 0.75 线/未注册 u 韵不唤醒/
       阈值 env 可调
    T5 旧签名锁: detect_wake 原语义零破坏(53号调用点保障)
    T6 fail-soft: 评分异常回退旧判定
    T7 分值落库: turn 持久化 wakeScore(extras 透传优先/
       其余轮现算回填/fail-soft 兜底/反序列化往返)
       ——P3-1 判定矩阵数据源, P1 原设计 extras 已带
       但 _save_turn 未落库, 本批补
"""

import os

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("XIAOZHU_WAKE_SCORE_MODE", None)
os.environ.pop("XIAOZHU_WAKE_THRESHOLD", None)

import pytest

from services.xiaozhu_service import (
    detect_wake, detect_wake_v2, wake_score, wake_score_mode,
    wake_threshold, WAKE_THRESHOLD_DEFAULT,
)


def test_t1_score_grading():
    """T1 分级评分表"""
    assert wake_score("小竹，看新品") == 1.0
    assert wake_score("小竹竹，查优惠") == 0.9
    assert wake_score("晓竹看新品") == 0.9
    assert wake_score("小朱 看新品") == 0.8
    assert wake_score("小猪看新品") == 0.8
    assert wake_score("小珠查订单") == 0.8
    assert wake_score("小主，查优惠") == 0.7
    # 未注册 u 韵(观察留痕不唤醒)
    assert wake_score("小住一下") == 0.55
    assert wake_score("小助帮我") == 0.55
    # 无唤醒意象
    assert wake_score("帮我看看新品") == 0.0
    assert wake_score("") == 0.0


def test_t2_mid_sentence_decay():
    """T2 句中命中位置衰减"""
    # 前 1/3 命中: ×0.75(小竹 1.0 → 0.75)
    assert wake_score("请小竹看新品") == 0.75
    # 中后段命中: ×0.65(小猪 0.8 → 0.52)
    assert wake_score("帮我看看新产品。小猪，看看新品") == 0.52


def test_t3_off_zero_diff(monkeypatch):
    """T3 off(默认): v2 判定与旧 detect_wake 全等"""
    monkeypatch.delenv("XIAOZHU_WAKE_SCORE_MODE", raising=False)
    assert wake_score_mode() is False
    cases = [
        "小竹，看新品", "小朱 看新品", "小猪看新品",
        "小竹竹, 查优惠", "小主，查优惠", "晓竹看新品",
        "小珠查订单", "帮我看看新品", "", "小竹",
        "小住一下",            # 未注册 u 韵
        "看看新产品。小猪，看看新品",   # 句中
    ]
    for t in cases:
        old = detect_wake(t)
        new = detect_wake_v2(t)
        assert (new[0], new[1]) == (old[0], old[1]), \
            f"off 态行为差异: {t!r} old={old} new={new[:2]}"
        assert 0.0 <= new[2] <= 1.0


def test_t4_on_threshold(monkeypatch):
    """T4 on 态: 阈值分值判定(0.7 线=旧表命中面完全等价)"""
    monkeypatch.setenv("XIAOZHU_WAKE_SCORE_MODE", "on")
    assert wake_score_mode() is True
    assert wake_threshold() == WAKE_THRESHOLD_DEFAULT == 0.7
    # 表内命中面全过(≥0.7, 含弱近似"小主")
    for t in ("小竹，看新品", "小猪看新品", "小主，查优惠",
              "小竹竹，查优惠"):
        woken, cmd, score = detect_wake_v2(t)
        assert woken is True, f"表内音未唤醒: {t}"
        assert score >= 0.7
    # 未注册 u 韵: 留痕不唤醒(与旧表外行为一致)
    woken, _, score = detect_wake_v2("小住一下")
    assert woken is False and score == 0.55
    # 阈值 env 可调(调低后 u 韵音唤醒——灰度可调能力)
    monkeypatch.setenv("XIAOZHU_WAKE_THRESHOLD", "0.5")
    assert wake_threshold() == 0.5
    woken2, _, _ = detect_wake_v2("小住一下")
    assert woken2 is True
    # 越界钳制
    monkeypatch.setenv("XIAOZHU_WAKE_THRESHOLD", "2.0")
    assert wake_threshold() == 1.0


def test_t5_legacy_signature_locked():
    """T5 旧 detect_wake 原语义零破坏(53号 login53 调用点保障)"""
    assert detect_wake("小竹，看新品") == (True, "看新品")
    assert detect_wake("帮我看看新品") == (False, "帮我看看新品")
    assert detect_wake("小竹") == (True, "")


def test_t6_fail_soft(monkeypatch):
    """T6 fail-soft: 评分异常回退旧判定(v2 不炸)"""
    import services.xiaozhu_service as xz
    monkeypatch.delenv("XIAOZHU_WAKE_SCORE_MODE", raising=False)

    def _boom(_):
        raise RuntimeError("score crash")

    monkeypatch.setattr(xz, "wake_score", _boom)
    woken, cmd, score = detect_wake_v2("小竹，看新品")
    assert woken is True and cmd == "看新品"   # 旧轨兜底


async def test_t7_wakescore_persisted(monkeypatch):
    """T7 分值落库: _save_turn 持久化 wakeScore"""
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    from services.xiaozhu_service import XiaozhuService

    svc = XiaozhuService()
    repo = Xiaozhu48Repository()
    s = await svc.open_session(701, "text")
    sid = s["sessionId"]
    session = await repo.get_session(sid)

    # ① extras 透传优先: not_woken 轮携带 v2 实算值
    r1 = await svc._save_turn(
        session, "voice", "小住一下", "not_woken",
        {"reply": "我在"}, {"wakeScore": 0.55})
    assert r1["turn"]["wakeScore"] == 0.55
    # ② 其余轮现算回填: 命令轮同函数确定性回放
    r2 = await svc._save_turn(
        session, "voice", "小猪看新品", "product.new",
        {"reply": "好"}, {})
    assert r2["turn"]["wakeScore"] == 0.8
    # ③ fail-soft: 评分异常不阻落轮, 兜底 0.0
    import services.xiaozhu_service as xz

    def _boom(_):
        raise RuntimeError("score crash")

    monkeypatch.setattr(xz, "wake_score", _boom)
    r3 = await svc._save_turn(
        session, "voice", "小竹，看新品", "chat",
        {"reply": "ok"}, {})
    assert r3["turn"]["wakeScore"] == 0.0
    monkeypatch.undo()
    # ④ 持久化往返: list_turns 取回 float(反序列化口径)
    turns = await repo.list_turns(sid)
    scores = {t["seq"]: t.get("wakeScore") for t in turns}
    assert scores[r1["turn"]["seq"]] == 0.55
    assert scores[r2["turn"]["seq"]] == 0.8
    assert scores[r3["turn"]["seq"]] == 0.0
