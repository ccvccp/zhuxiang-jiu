"""48号·小竹语音 v80~v86 全量复测脚本

覆盖三轮文档借鉴落地项的服务端可测面:
    - v80 度数意向过滤(extract_abv/推荐度数接近/集成 56→53)
    - v80 member 级免唤醒窗(点亮/超窗打回)
    - v81 商品定位前置(关键词提取/miss 明确回答/指名直入/
      问价 miss)
    - v83 查一款句式入 rule 轨(防"查个订单"误入)
    - v83-B 虚假执行守卫(正则面)
    - v86-A 语气词前置防御(不进 LLM 语义解析)
    - v86-B 钩子熔断(wait_for 框架行为冒烟)
    - 前端真机复测清单(SM 徽标/打断埋点/哑流自愈)

运行方式:
    python test_xiaozhu_v86_recheck.py
"""

import asyncio
import os
import sys
import time

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


async def _open(member_id: int = 1) -> int:
    from services.xiaozhu_service import XiaozhuService
    r = await XiaozhuService().open_session(member_id)
    return r["sessionId"]


async def _text(sid: int, text: str) -> dict:
    from services.xiaozhu_service import XiaozhuService
    return await XiaozhuService().handle_text(sid, text)


class TestV80Abv:
    async def run(self):
        print("[01 v80 度数意向过滤]")
        from services.xiaozhu_wine_service import extract_abv
        record("extract_abv 四态(56度/42°/容量/预算排除)",
               extract_abv("选一款56度的酒") == 56
               and extract_abv("来一件42°的") == 42
               and extract_abv("500ml") is None
               and extract_abv("预算800元") is None,
               "")
        sid = await _open()
        r = await _text(sid, "小竹，选一款56度的酒")
        record("集成: 56度→53度接近款(非42/45热销)",
               "53度" in str(r.get("reply")),
               str(r.get("reply"))[:60])
        r = await _text(sid, "小竹，选一款42度的酒")
        record("集成: 42度→42度款命中",
               "42度" in str(r.get("reply")),
               str(r.get("reply"))[:60])


class TestV80MemberWindow:
    async def run(self):
        print("[02 v80 member 级免唤醒窗]")
        from services.xiaozhu_service import XiaozhuService
        sid = await _open()
        XiaozhuService._LAST_WAKE_AT[1] = time.time()
        r = await _text(sid, "看新品")  # 无唤醒词前缀
        record("窗内: 无前缀指令放行(不打回)",
               "请以「小竹」开头" not in str(r.get("reply")),
               str(r.get("reply"))[:50])
        XiaozhuService._LAST_WAKE_AT.pop(1, None)
        sid2 = await _open()
        r = await _text(sid2, "看新品")
        record("超窗: 重新唤醒提示(not_woken 打回)",
               "小竹" in str(r.get("reply"))
               and "开头" in str(r.get("reply")),
               str(r.get("reply"))[:50])


class TestV81Locate:
    async def run(self):
        print("[03 v81 商品定位前置]")
        from services.xiaozhu_service import (
            XiaozhuService,
        )
        kw1 = XiaozhuService._extract_product_kw(
            "竹香珍藏怎么样")
        kw2 = XiaozhuService._extract_product_kw(
            "推荐竹香经典")
        record("商品词提取(剥指令/疑问词)",
               kw1 == "竹香珍藏" and kw2 == "竹香经典",
               f"{kw1!r}|{kw2!r}")
        miss = await XiaozhuService._product_miss_reply("茅台")
        record("miss 明确回答(不静默热销)",
               "没有找到「茅台」" in miss["reply"]
               and "在售" in miss["reply"],
               miss["reply"][:60])
        sid = await _open()
        r = await _text(sid, "小竹，茅台多少钱")
        record("集成: 问价 miss → 明确回答",
               "没有找到" in str(r.get("reply"))
               and not (r.get("card") or {}).get("type"),
               str(r.get("reply"))[:50])
        r = await _text(sid, "小竹，推荐竹香经典")
        items = ((r.get("card") or {}).get("items") or [])
        record("集成: 指名直入(推荐竹香经典→1款该款)",
               len(items) == 1
               and "竹香经典" in str(
                   (items or [{}])[0].get("name")),
               f"n={len(items)}")


class TestV83Pattern:
    async def run(self):
        print("[04 v83 查一款句式]")
        from services.xiaozhu_service import match_command
        record("「查一款52度的酒」→ wine.recommend",
               (match_command("查一款52度的酒") or {})
               .get("action") == "wine.recommend", "")
        record("「查个订单」不误入荐酒(查个未收)",
               (match_command("小竹，查个订单") or {})
               .get("action") != "wine.recommend",
               str((match_command("小竹，查个订单") or {})
                   .get("action")))
        sid = await _open()
        r = await _text(sid, "小竹，查一款42度的酒")
        record("集成: 查一款走 rule 轨(快+规范回复)",
               "42度附近挑了" in str(r.get("reply")),
               f"{str(r.get('reply'))[:50]}")


class TestV83Guard:
    async def run(self):
        print("[05 v83 虚假执行守卫(正则面)]")
        import re
        guard = re.compile(
            r"已加|已下单|已提交|已结算|已为您下单"
            r"|已放进购物车")
        record("执行话术命中(已加「竹奕」)",
               bool(guard.search("已加「竹奕·竹香尊享 5")),
               "")
        record("正常话术不命中(好的，为您推荐)",
               not guard.search("好的，为您推荐一款"),
               "")
        print("    (集成路径依赖 LLM 轨——见真机清单)")


class TestV86Filler:
    async def run(self):
        print("[06 v86 语气词前置防御]")
        from services.xiaozhu_service import _is_filler
        record("_is_filler 判定(嗯/啊=真, 指令=假)",
               _is_filler("嗯") and _is_filler("啊")
               and not _is_filler("查订单"),
               "")
        sid = await _open()
        r = await _text(sid, "小竹，嗯")
        record("集成: 语气词轮静默请重讲(不进语义解析)",
               "没太听清" in str(r.get("reply")),
               str(r.get("reply"))[:50])


class TestV86Timeout:
    async def run(self):
        print("[07 v86 钩子熔断(框架行为冒烟)]")

        async def slow_hook():
            await asyncio.sleep(2)
            return {"reply": "slow"}

        t0 = time.monotonic()
        try:
            await asyncio.wait_for(slow_hook(), timeout=0.8)
            record("慢钩子 800ms 熔断", False, "未超时")
        except asyncio.TimeoutError:
            ms = round((time.monotonic() - t0) * 1000)
            record("慢钩子 800ms 熔断(超时捕获)",
                   750 <= ms <= 1200, f"ms={ms}")


class TestChecklist:
    async def run(self):
        print("[08 前端真机复测清单]")
        print("    □ SM 状态徽标流转: 灰待机→绿聆听→黄处理"
              "→蓝播报→绿(续问)→灰(关窗)")
        print("    □ 播报中打断: 徽标立即跳绿(barge_cut), "
              "console [LAT-BAR] cut_after_ms")
        print("    □ [SM] ILLEGAL 留痕: 竞态场景 console 可见"
              "(观察期数据)")
        print("    □ 面板哑流自愈: '没听清'时先弹"
              "'重新校准麦克风'后恢复([P-AHM])")
        print("    □ PROCESSING 看门狗: 断网 15s → "
              "'网络慢了半拍请再说一遍'")
        print("    □ member 窗直入: 关窗后 5 分钟内直接说"
              "「换一款」→ 自动弹面板直达")
        print("    □ 查一款/换一款/来一件/茅台 miss 各一轮")


async def main():
    tests = [TestV80Abv(), TestV80MemberWindow(),
             TestV81Locate(), TestV83Pattern(),
             TestV83Guard(), TestV86Filler(),
             TestV86Timeout(), TestChecklist()]
    for t in tests:
        reset_all()
        await t.run()
    print("\n" + "=" * 56)
    for line in RESULTS:
        print(line)
    print("=" * 56)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
