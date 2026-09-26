"""48号·小竹语音 42 度指令专项验证

背景: v80 度数排序 UnboundLocalError——ina(±1) 命中分支
    漏赋 _abv_sorted, 42 度类指令自 v80 起全部炸
    "数据源波动"; v86 修复(前置初始化+两分支统一终排序)。
    本脚本专项回归该路径族:

    [A] 度数边界矩阵: 41/42/43(±1 命中, 原 bug 路径)/
        40/44/56(排序路径, 44 实命中 45 度款)
    [B] 多句式入口: 选一款/查一款/推荐 一款 × 42 度
    [C] 指名×度数混合: 指名直入胜出(度数被跳过不炸)
    [D] 集成链路: handle_text 全链(唤醒→路由→钩子→回复)
    [E] 度数+价格复合: 42 度 + 预算
    [F] 加购带度数: "来一件42度的"(kw 路径不炸)

运行方式:
    python test_xiaozhu_42deg.py
"""

import asyncio
import os
import sys

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


async def _recommend(text):
    from services.xiaozhu_wine_service import (
        XiaozhuWineService,
    )
    r = await XiaozhuWineService().recommend(text)
    return r


def _alcohols(r):
    items = ((r.get("card") or {}).get("items") or [])
    return [float(i.get("alcohol") or 0)
            for i in items if i.get("alcohol")]


def _no_data_flap(r):
    return "数据源波动" in str(r.get("reply"))


class TestAAbvMatrix:
    async def run(self):
        print("[A 度数边界矩阵(recommend 直调)]")
        cases = [
            # (输入度数, 期望: 命中集合或排序断言, 说明)
            (42, {42}, "42 命中(原 UnboundLocalError 路径)"),
            (43, {42}, "43 → 42(+1 边界命中)"),
            (41, {42}, "41 → 42(-1 边界命中)"),
            (44, {45}, "44 → 45(+1 边界命中)"),
        ]
        for deg, want, desc in cases:
            r = await _recommend(f"选一款{deg}度的酒")
            got = set(_alcohols(r))
            record(f"{desc}",
                   not _no_data_flap(r) and got == want,
                   f"alcohols={sorted(got)} "
                   f"reply={str(r.get('reply'))[:40]}")
        # 排序路径(ina 空 → 度数接近为主键)
        r = await _recommend("选一款40度的酒")
        got = _alcohols(r)
        record("40 → 接近排序 42 首位(ina 空路径)",
               not _no_data_flap(r)
               and got and got[0] == 42,
               f"alcohols={got}")
        r = await _recommend("选一款56度的酒")
        got = _alcohols(r)
        record("56 → 接近排序 53 首位",
               not _no_data_flap(r)
               and got and got[0] == 53,
               f"alcohols={got}")


class TestBPhrases:
    async def run(self):
        print("[B 多句式入口 × 42 度]")
        for phrase in ("选一款42度的酒", "查一款42度的酒",
                       "推荐一款42度的酒"):
            r = await _recommend(phrase)
            record(f"「{phrase}」→ 42 度款",
                   not _no_data_flap(r)
                   and 42 in _alcohols(r),
                   str(r.get("reply"))[:40])


class TestCMixed:
    async def run(self):
        print("[C 指名×度数混合]")
        r = await _recommend("推荐竹香经典")
        items = ((r.get("card") or {})
                 .get("items") or [])
        record("纯指名: 直入竹香经典 1 款",
               not _no_data_flap(r)
               and len(items) == 1,
               f"n={len(items)}")
        r = await _recommend("选一款42度的竹香经典")
        items = ((r.get("card") or {})
                 .get("items") or [])
        record("指名+度数: 指名胜出(度数跳过不炸)",
               not _no_data_flap(r) and len(items) >= 1,
               str(r.get("reply"))[:40])


class TestDIntegration:
    async def run(self):
        from repositories.store import reset_store
        from services.xiaozhu_service import (
            XiaozhuService, match_command,
        )
        print("[D 集成链路(handle_text 全链)]")
        record("路由: 「查一款42度的酒」→ wine.recommend",
               (match_command("查一款42度的酒") or {})
               .get("action") == "wine.recommend", "")
        reset_store()
        sid = (await XiaozhuService()
               .open_session(1))["sessionId"]
        r = await XiaozhuService().handle_text(
            sid, "小竹，选一款42度的酒")
        record("集成: 唤醒→路由→钩子→42 度回复",
               "42度" in str(r.get("reply"))
               and not _no_data_flap(r),
               str(r.get("reply"))[:50])
        r = await XiaozhuService().handle_text(
            sid, "小竹，查一款42度的酒")
        record("集成: 查一款 42 度",
               "42度" in str(r.get("reply"))
               and not _no_data_flap(r),
               str(r.get("reply"))[:50])


class TestEComposite:
    async def run(self):
        print("[E 度数+预算复合]")
        r = await _recommend("选一款42度预算300以内的酒")
        got = _alcohols(r)
        record("42度+预算300: 42 度款(268 元在预算内)",
               not _no_data_flap(r)
               and 42 in got,
               f"alcohols={got} "
               f"reply={str(r.get('reply'))[:40]}")


class TestFCartWithAbv:
    async def run(self):
        from repositories.store import reset_store
        from services.xiaozhu_service import (
            XiaozhuService,
        )
        print("[F 加购带度数(不炸为底线)]")
        reset_store()
        sid = (await XiaozhuService()
               .open_session(1))["sessionId"]
        r = await XiaozhuService().handle_text(
            sid, "小竹，看新品")
        r = await XiaozhuService().handle_text(
            sid, "小竹，来一件42度的")
        record("加购带度数: 路径不炸(加购或引导均可)",
               not _no_data_flap(r),
               str(r.get("reply"))[:50])


class TestProdChecklist:
    async def run(self):
        print("[G 生产真机验证清单]")
        print("    □ 「小竹，选一款42度的酒」→ 播报 42 度款"
              "(非'数据源波动')")
        print("    □ 「小竹，查一款42度的酒」→ 同上(查一款句式)")
        print("    □ 「小竹，选一款43度的酒」→ 42 度款(边界)")
        print("    □ 「小竹，茅台多少钱」→ 没有找到(对照 miss)")
        print("    □ 播报完整流畅 + 状态徽标流转正常")


async def main():
    tests = [TestAAbvMatrix(), TestBPhrases(),
             TestCMixed(), TestDIntegration(),
             TestEComposite(), TestFCartWithAbv(),
             TestProdChecklist()]
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
