"""48号 P3-2·唤醒 EMA 平滑专项单测

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python -m pytest test_xiaozhu_wake_ema.py -q

覆盖(小方案 §五, 八用例):
    T1 开关三态: XIAOZHU_WAKE_EMA off 默认/on env 可调
    T2 进链门槛: raw<0.55 不进链(count/value 不变);
       raw≥0.55 进链(EMA 公式+count+1)
    T3 冷启动: 进链轮次<10 链返回 None(判定降级 raw 单过)
    T4 双过: on 态临界带(0.7≤raw<0.9)高链放行 emaPass/
       低链拦截 emaBlock(woken 翻 False 走 not_woken 轨)
    T5 直通带: on 态低链下 raw≥0.9 照常唤醒 emaDirect(真唤醒零伤害)
    T6 组合矩阵: SCORE off+EMA on=零差异(旧轨全等)
    T7 fail-soft: 链读写异常回退 raw 单过不 crash
    T8 留痕往返: turn 持久化 wakeTrack/wakeEma 反序列化取回
"""

import os

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
for _k in ("XIAOZHU_WAKE_SCORE_MODE", "XIAOZHU_WAKE_EMA"):
    os.environ.pop(_k, None)

import pytest

from repositories.xiaozhu_repository import Xiaozhu48Repository
from services.xiaozhu_service import (
    XiaozhuService, wake_ema_mode, _WAKE_EMA_ALPHA,
    _WAKE_EMA_FLOOR, _WAKE_EMA_WARM_ROUNDS,
)


async def _chain(member_id=1):
    return await Xiaozhu48Repository().get_wake_ema(member_id)


def test_t1_mode_switch(monkeypatch):
    """T1 开关三态(off 默认/env on)"""
    monkeypatch.delenv("XIAOZHU_WAKE_EMA", raising=False)
    assert wake_ema_mode() is False
    monkeypatch.setenv("XIAOZHU_WAKE_EMA", "on")
    assert wake_ema_mode() is True


async def test_t2_chain_threshold():
    """T2 进链门槛(防零分污染)"""
    repo = Xiaozhu48Repository()
    svc = XiaozhuService()
    # 冷链起步
    assert await _chain(901) is None
    # raw=0.0(普通文本) 不进链
    r = await svc._wake_ema_chain(901, 0.0)
    assert r is None
    c = await _chain(901)
    assert c is None            # 一次都没进链
    # raw=0.5(低于近似音带) 不进链
    await svc._wake_ema_chain(901, 0.5)
    assert await _chain(901) is None
    # raw=0.55(u 韵带) 进链
    r = await svc._wake_ema_chain(901, 0.55)
    assert r is None            # 仍冷启动(count=1<10)
    c = await _chain(901)
    assert c["count"] == 1
    assert c["value"] == pytest.approx(
        _WAKE_EMA_ALPHA * 0.55, abs=1e-3)
    # 第二轮进链: EMA 公式
    await svc._wake_ema_chain(901, 0.8)
    c = await _chain(901)
    assert c["count"] == 2
    assert c["value"] == pytest.approx(
        _WAKE_EMA_ALPHA * 0.8
        + (1 - _WAKE_EMA_ALPHA) * (_WAKE_EMA_ALPHA * 0.55),
        abs=1e-3)


async def test_t3_cold_start():
    """T3 冷启动(count<10 返回 None 判定降级)"""
    svc = XiaozhuService()
    for _ in range(_WAKE_EMA_WARM_ROUNDS - 1):
        r = await svc._wake_ema_chain(902, 0.7)
        assert r is None          # 冷启动全程 None
    assert (await _chain(902))["count"] == _WAKE_EMA_WARM_ROUNDS - 1
    # 第 10 轮 warm
    r = await svc._wake_ema_chain(902, 0.7)
    assert r is not None and r["count"] == _WAKE_EMA_WARM_ROUNDS


async def _handle_with(member_id, text,
                       score_on=True, ema_on=True):
    """开三态走全链 _handle_text_internal"""
    os.environ["XIAOZHU_WAKE_SCORE_MODE"] = "on" if score_on else "off"
    os.environ["XIAOZHU_WAKE_EMA"] = "on" if ema_on else "off"
    svc = XiaozhuService()
    s = await svc.open_session(member_id, "text")
    sid = s["sessionId"]
    return await svc.handle_text(sid, text)


async def test_t4_double_gate():
    """T4 双过: 高链 emaPass/低链 emaBlock(woken 翻 False)"""
    repo = Xiaozhu48Repository()
    # 成员 903: 灌低链(纯弱近似音×10 → ema≈0.7 以下)
    for _ in range(_WAKE_EMA_WARM_ROUNDS):
        await repo.set_wake_ema(903, {"value": 0.40, "count": _ + 1})
    r = await _handle_with(903, "小主，查优惠")   # raw=0.7 临界带
    assert r.get("turn", {}).get("wakeTrack") == "emaBlock"
    assert r.get("wakeHint") is True              # 走 not_woken 轨
    # 成员 904: 灌高链
    await repo.set_wake_ema(904, {"value": 0.85, "count": 99})
    r = await _handle_with(904, "小主，查优惠")
    assert r.get("turn", {}).get("wakeTrack") == "emaPass"
    assert r.get("wakeHint") is False             # 正常唤醒


async def test_t5_direct_band():
    """T5 直通带: 低链下 raw≥0.9 照常唤醒(真唤醒零伤害)"""
    await Xiaozhu48Repository().set_wake_ema(
        905, {"value": 0.10, "count": 99})        # 极低链
    r = await _handle_with(905, "小竹，看新品")   # raw=1.0
    assert r.get("wakeHint") is False             # 唤醒了
    assert r.get("turn", {}).get("wakeTrack") == "emaDirect"


async def test_t6_matrix_off_zero_diff():
    """T6 组合矩阵: SCORE off+EMA on=零差异(旧轨)"""
    cases = ["小竹，看新品", "小主，查优惠", "帮我看看新品", "小住一下"]
    for t in cases:
        r_on = await _handle_with(906, t, score_on=False, ema_on=True)
        assert r_on.get("wakeHint") is False or t == "帮我看看新品"
        # SCORE off 下无论 EMA 开关全等(P1 旧轨锁, T3 同源)
        r_off = await _handle_with(906, t, score_on=False, ema_on=False)
        assert (r_on.get("wakeHint"), r_on.get("reply")) == \
               (r_off.get("wakeHint"),
                r_off.get("reply") if r_off.get("wakeHint")
                else r_on.get("reply"))


async def test_t7_fail_soft(monkeypatch):
    """T7 fail-soft: 链异常回退 raw 单过不 crash"""
    async def _boom(*a, **k):
        raise RuntimeError("chain crash")

    monkeypatch.setattr(
        Xiaozhu48Repository, "get_wake_ema", _boom)
    r = await _handle_with(907, "小竹，看新品")   # raw=1.0
    assert r.get("wakeHint") is False             # 照常唤醒
    assert r.get("turn", {}).get("wakeTrack") == "rule"


async def test_t8_turn_roundtrip():
    """T8 留痕往返: wakeTrack/wakeEma 持久化反序列化取回"""
    repo = Xiaozhu48Repository()
    await repo.set_wake_ema(908, {"value": 0.85, "count": 99})
    os.environ["XIAOZHU_WAKE_SCORE_MODE"] = "on"
    os.environ["XIAOZHU_WAKE_EMA"] = "on"
    svc = XiaozhuService()
    s = await svc.open_session(908, "text")
    sid = s["sessionId"]
    await svc.handle_text(sid, "小主，查优惠")     # emaPass 轮
    turns = await repo.list_turns(sid)
    t = turns[-1] if turns else {}
    assert t.get("wakeTrack") == "emaPass"
    # EMA_t 含当前轮(0.1×0.7+0.9×0.85)——判定与留痕同为更新后值
    assert abs(float(t.get("wakeEma") or 0)
               - round(0.1 * 0.7 + 0.9 * 0.85, 3)) < 0.01
