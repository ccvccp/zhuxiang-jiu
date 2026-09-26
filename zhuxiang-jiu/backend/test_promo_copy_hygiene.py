"""36号·文案卫生专项回归(脚本结构标记治理, 2026-09-27)

背景: douyin 规则模板原为短视频脚本结构, 【0-3s 钩子】等分镜
时间标记随内容 #30 泄漏到公开图文笔记(正文+封面)。三层治理:
    [A] 生成源头: douyin 规则模板/画像 format 改图文口径
    [B] 确定性防线: _strip_script_markers 剥离函数
    [C] 产出出口: generate_platform_contents 双轨统一剥除
       (monkey-patch 模拟 GLM 轨漏网, 验证出口兜底)

运行: python test_promo_copy_hygiene.py
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  OK {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  FAIL {name} -- {detail}")


class TestASource:
    async def run(self):
        print("[A 生成源头(模板+画像口径)]")
        from repositories.promo_repository import (
            REQUIRED_DISCLAIMER, REQUIRED_AGE_TIP,
            PROMO_PLATFORM_DOUYIN,
        )
        from services.promo_agent_service import (
            _RULE_TEMPLATES, PLATFORM_PROFILES, _SCRIPT_MARKER_RE,
        )
        tpl = _RULE_TEMPLATES[PROMO_PLATFORM_DOUYIN].format(
            title="中秋团圆宴白酒清单火了",
            disclaimer=REQUIRED_DISCLAIMER, age=REQUIRED_AGE_TIP)
        record("douyin 规则模板无脚本时间标记",
               not _SCRIPT_MARKER_RE.search(tpl), f"tpl={tpl!r}")
        record("模板保留警示语与年龄提示",
               REQUIRED_DISCLAIMER in tpl
               and REQUIRED_AGE_TIP in tpl, f"tpl={tpl!r}")
        fmt = PLATFORM_PROFILES[PROMO_PLATFORM_DOUYIN]["format"]
        record("douyin 画像 format 图文口径(无视频脚本)",
               "视频脚本" not in fmt and "图文" in fmt, f"format={fmt}")


class TestBStrip:
    async def run(self):
        print("[B 剥离函数(_strip_script_markers)]")
        from services.promo_agent_service import _strip_script_markers
        cases = [
            ("【0-3s 钩子】最近刷屏了!", "最近刷屏了!"),
            ("【3-10s 卖点】入口绵甜\n【10-15s 行动】点击主页",
             "入口绵甜\n点击主页"),
            ("【15s 转折】但…", "但…"),
            ("无标记文案原样保留", "无标记文案原样保留"),
            ("", ""),
        ]
        for src, want in cases:
            got = _strip_script_markers(src)
            record(f"剥离 {src[:14]!r}", got == want,
                   f"got={got!r} want={want!r}")
        keep = "【竹香型白酒】入口绵甜"
        record("正常【】品牌文本不误伤",
               _strip_script_markers(keep) == keep,
               f"got={_strip_script_markers(keep)!r}")


class TestCExit:
    async def run(self):
        print("[C 产出出口(generate_platform_contents 兜底)]")
        from repositories.promo_repository import (
            PROMO_PLATFORM_DOUYIN, REQUIRED_DISCLAIMER, REQUIRED_AGE_TIP,
        )
        from services.promo_agent_service import PromoAgentService
        agent = PromoAgentService()
        original = agent.generate_draft
        # 模拟 GLM 轨漏网: 草稿带脚本时间标记(合规尾巴完整)
        agent.generate_draft = lambda *a, **k: ({
            "title": "脚本标记泄漏探针",
            "body": ("【0-3s 钩子】最近 \"中秋\" 刷屏了!\n"
                     "【3-10s 卖点】竹香型白酒入口绵甜。\n"
                     f"（{REQUIRED_DISCLAIMER}，未成年人禁止饮酒，"
                     f"满{REQUIRED_AGE_TIP}周岁请适量）"),
            "hashtags": "#竹香型白酒", "cta": "", "coverHint": "",
        }, "glm-test")
        try:
            results = await agent.generate_platform_contents(
                {"hotspotId": 1, "title": "中秋热点",
                 "summary": "", "heat": 100, "brandHits": ["白酒"]},
                platforms=(PROMO_PLATFORM_DOUYIN,))
            body = results[0]["body"]
            record("出口剥除: 无【N-Ms 标记",
                   "【0-3s" not in body and "【3-10s" not in body,
                   f"body={body!r}")
            record("出口剥除: 标记后句子保留",
                   "刷屏了" in body and "入口绵甜" in body,
                   f"body={body!r}")
            record("出口剥除: 合规尾巴保留",
                   REQUIRED_DISCLAIMER in body, f"body={body!r}")
        finally:
            agent.generate_draft = original


async def main():
    tests = [TestASource(), TestBStrip(), TestCExit()]
    for t in tests:
        await t.run()
    print("\n" + "=" * 56)
    for line in RESULTS:
        print(line)
    print("=" * 56)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
