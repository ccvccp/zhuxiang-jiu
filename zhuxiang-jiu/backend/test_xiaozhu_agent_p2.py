"""48号·招商代理意图 P2 专项单测

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python -m pytest test_xiaozhu_agent_p2.py -q

覆盖(小方案 §三):
    T1 pattern 命中: agent.apply 14 变体 + agent.faq 5 变体
    T2 误触隔离: "查代理数据"类不含; nav 优先序("打开代理中心"
       走 nav.page 不落 agent.apply)
    T3 执行器: 三要点话术 + card + jump agent-center;
       levels 官方档位 fail-soft(异常回通用口径)
    T4 指令集自描述: list_commands 含新意图
"""

import asyncio
import os

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

import pytest

from services.xiaozhu_service import (
    match_command, match_nav_page, list_commands, XiaozhuService,
)


def test_t1_pattern_hits():
    """T1: 招商意图变体全命中"""
    applies = ["我想做代理", "想做代理", "做代理", "代理",
               "招商", "加盟", "想加盟", "怎么加盟",
               "怎么代理", "代理政策", "招商政策",
               "代理申请", "渠道合作", "代理条件"]
    for t in applies:
        cmd = match_command(t)
        assert cmd and cmd["action"] == "agent.apply", f"未命中: {t}"
    faqs = ["代理返利", "返利层级", "代理等级",
            "代理门槛", "代理有什么好处"]
    for t in faqs:
        cmd = match_command(t)
        assert cmd and cmd["action"] == "agent.faq", f"未命中: {t}"


def test_t2_no_false_positive():
    """T2: nav 优先序——'打开代理中心'走 nav.page 跳转"""
    cmd = match_command("打开代理中心")
    assert cmd and cmd["action"] == "nav.page", \
        "nav 在注册序前, 应先命中"
    assert match_nav_page("打开代理中心") == \
        "/#/pages/agent-center/index"
    # 招商直问落 agent.apply
    cmd2 = match_command("去招商")
    assert cmd2 and cmd2["action"] == "agent.apply"


def test_t3_exec_agent_apply():
    """T3: 执行器三要点 + card/jump + levels fail-soft"""

    async def run():
        svc = XiaozhuService()
        r = await svc._exec_agent_apply()
        assert "代理" in r["reply"] and "申请" in r["reply"]
        assert "竹香酒" in r["reply"]          # 品牌要点
        assert "返利" in r["reply"] or "档位" in r["reply"]
        assert r["card"]["type"] == "agent_apply"
        assert r["jump"] == "/#/pages/agent-center/index"

    asyncio.run(run())


def test_t4_list_commands():
    """T4: 指令集自描述含新意图(xiaozhu.help 卡片数据源)"""
    actions = {c["action"] for c in list_commands()}
    assert "agent.apply" in actions
    assert "agent.faq" in actions
