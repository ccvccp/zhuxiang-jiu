"""网站图标智能管理模块端到端测试(Service 层直调, 不依赖 fastapi)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_site_theme_e2e.py

覆盖:
    1. 主题CRUD(5):   创建/默认主题存在/字段校验/编辑draft/active锁定编辑
    2. AI评估(4):     高分主题通过/低对比度拒绝激活/因子明细/评分落库
    3. 激活流转(4):   激活成功+原active归档/重复激活幂等/C端拉取新主题/
                      兜底默认主题
    4. 审计回滚(4):   日志列表/激活回滚/编辑回滚/创建日志无快照拒绝
    5. AI推荐(2):     节月份推荐/全因子完整性
    6. 图标库(1):     公开只读列表
"""

import asyncio
import os
from datetime import UTC, datetime

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.site_theme_service import SiteThemeService
from repositories.site_theme_repository import SiteThemeRepository
from repositories.store import reset_store as _reset_store_impl

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


def reset_store():
    _reset_store_impl()


async def _expect_value_error(coro, keyword=""):
    try:
        await coro
        return False, ""
    except ValueError as exc:
        return (not keyword or keyword in str(exc)), str(exc)
    except Exception as exc:
        return False, f"非ValueError: {type(exc).__name__}: {exc}"


async def _expect_key_error(coro):
    try:
        await coro
        return False, ""
    except KeyError:
        return True, ""
    except Exception as exc:
        return False, f"非KeyError: {type(exc).__name__}: {exc}"


# 高分配色(竹绿变体, WCAG 达标)
GOOD_COLORS = {
    "primary": "#2a4534", "primaryLight": "#4a7c59",
    "navBar": "#2a4534", "tabSelected": "#2a4534",
    "tabColor": "#999999", "tabBg": "#ffffff", "textOnPrimary": "#ffffff",
}

# 低对比度配色(主色与文本同为深色, WCAG 不达标)
BAD_CONTRAST_COLORS = {
    "primary": "#1d1d1d", "primaryLight": "#2a2a2a",
    "navBar": "#1d1d1d", "tabSelected": "#1d1d1d",
    "tabColor": "#999999", "tabBg": "#ffffff", "textOnPrimary": "#1a1a1a",
}

GOOD_ICONS = {"tabHome": "home", "tabProducts": "products", "tabMine": "mine"}


async def main():
    svc = SiteThemeService()
    repo = SiteThemeRepository()
    reset_store()
    admin = 88

    # ========================================================
    # 1. 主题 CRUD
    # ========================================================
    print("\n========== 1. 主题 CRUD ==========")

    # test 1: 默认主题(竹绿经典)存在且 active
    active = await svc.get_active_theme()
    record("test_01_default_theme_active",
           active["success"] and active["name"] == "竹绿经典"
           and active["colors"]["primary"] == "#355c44",
           f"active={active}")

    # test 2: 创建主题成功(初始 draft)
    t = await svc.create_theme(admin, "测试主题A", GOOD_COLORS, GOOD_ICONS,
                               "测试方案")
    record("test_02_create_theme_draft",
           t["status"] == "draft" and t["themeId"] >= 2
           and t["createdAdminId"] == admin,
           f"theme={t}")

    # test 3: 字段校验(颜色格式非法拒绝)
    bad_colors = dict(GOOD_COLORS, primary="355c44")  # 缺 #
    ok, msg = await _expect_value_error(
        svc.create_theme(admin, "坏主题", bad_colors), "RRGGBB")
    record("test_03_invalid_color_rejected", ok, msg)

    # test 4: 编辑 draft 成功
    t2 = await svc.update_theme(admin, t["themeId"],
                                description="更新后的描述")
    record("test_04_update_draft",
           t2["description"] == "更新后的描述", f"theme={t2}")

    # test 5: active 主题锁定编辑
    default_theme = await repo.get_active_theme()
    ok, msg = await _expect_value_error(
        svc.update_theme(admin, default_theme["themeId"],
                         description="试图改激活主题"), "锁定编辑")
    record("test_05_active_locked", ok, msg)

    # ========================================================
    # 2. AI 健康度评估
    # ========================================================
    print("\n========== 2. AI 健康度评估 ==========")

    # test 6: 高分主题 AI 评估通过
    check = await svc.ai_check(t["themeId"])
    record("test_06_ai_check_pass",
           check["passed"] and check["score"] >= 60
           and len(check["factors"]) == 5,
           f"check={check.get('score')}, factors={len(check['factors'])}")

    # test 7: AI 评分落库
    t3 = await repo.get_theme(t["themeId"])
    record("test_07_ai_score_saved",
           t3["aiScoreLatest"] == int(check["score"]),
           f"saved={t3['aiScoreLatest']}")

    # test 8: 低对比度主题被拒激活
    bad = await svc.create_theme(admin, "低对比度主题", BAD_CONTRAST_COLORS,
                                 GOOD_ICONS)
    ok, msg = await _expect_value_error(
        svc.activate_theme(admin, bad["themeId"]), "AI 健康度评分")
    record("test_08_low_contrast_blocked", ok, msg)

    # test 9: 主题不存在
    ok, _ = await _expect_key_error(svc.ai_check(99999))
    record("test_09_theme_not_found", ok)

    # ========================================================
    # 3. 激活流转
    # ========================================================
    print("\n========== 3. 激活流转 ==========")

    # test 10: 激活成功, 原 active 转 archived
    r = await svc.activate_theme(admin, t["themeId"])
    old_default = await repo.get_theme(1)
    new_active = await repo.get_active_theme()
    record("test_10_activate_success",
           r["success"] and new_active["themeId"] == t["themeId"]
           and old_default["status"] == "archived",
           f"r={r['success']}, new_active={new_active['themeId']}, "
           f"old_status={old_default['status']}")

    # test 11: 重复激活幂等
    r2 = await svc.activate_theme(admin, t["themeId"])
    record("test_11_activate_idempotent",
           r2["success"] and "已是激活状态" in r2.get("note", ""),
           f"r={r2}")

    # test 12: C 端拉取新激活主题
    active2 = await svc.get_active_theme()
    record("test_12_c_end_fetches_new_theme",
           active2["themeId"] == t["themeId"]
           and active2["colors"]["primary"] == "#2a4534",
           f"active={active2}")

    # ========================================================
    # 4. 审计与回滚
    # ========================================================
    print("\n========== 4. 审计与回滚 ==========")

    # test 13: 审计日志列表(create/update/activate 均有)
    logs = await svc.list_logs(theme_id=t["themeId"])
    actions = {l["action"] for l in logs}
    record("test_13_audit_logs",
           {"create", "update", "activate"} <= actions,
           f"actions={actions}")

    # test 14: 回滚激活操作(恢复为 draft, 默认主题仍是 archived)
    activate_log = next(l for l in logs if l["action"] == "activate")
    rb = await svc.rollback(admin, activate_log["logId"])
    t_after = await repo.get_theme(t["themeId"])
    record("test_14_rollback_activate",
           rb["success"] and t_after["status"] == "draft",
           f"status={t_after['status']}")

    # test 15: 回滚后归档默认主题可重新激活(AI 通过)
    r3 = await svc.activate_theme(admin, 1)
    record("test_15_reactivate_default",
           r3["success"] and r3["theme"]["themeId"] == 1,
           f"r={r3.get('success')}")

    # test 16: 创建日志无 before 快照, 回滚拒绝
    create_log = next(l for l in logs if l["action"] == "create")
    ok, msg = await _expect_value_error(
        svc.rollback(admin, create_log["logId"]), "无回滚前快照")
    record("test_16_create_log_no_rollback", ok, msg)

    # ========================================================
    # 5. AI 季节推荐
    # ========================================================
    print("\n========== 5. AI 季节推荐 ==========")

    # test 17: 春节月份(1月)推荐红金系
    rec = await svc.recommend(month=1)
    record("test_17_recommend_spring_festival",
           rec["success"] and rec["festival"] == "spring_festival"
           and rec["best"]["recommendScore"] > 0
           and len(rec["recommendations"]) == 3,
           f"best={rec['best']['name']}({rec['best']['recommendScore']})")

    # test 18: 通用月份推荐完整性(因子/reasons)
    rec2 = await svc.recommend()
    record("test_18_recommend_complete",
           rec2["success"] and "season" in rec2
           and all("reasons" in r and r["reasons"] for r
                   in rec2["recommendations"]),
           f"rec={rec2.get('season')}")

    # ========================================================
    # 6. 图标库(公开)
    # ========================================================
    print("\n========== 6. 图标库 ==========")

    # test 19: 图标库只读列表(含种子 emoji 图标)
    icons = await svc.list_icons()
    record("test_19_icons_public_list",
           isinstance(icons, list) and len(icons) > 0,
           f"count={len(icons)}")

    # test 20: 管理员新增 emoji 图标
    emoji_icon = await svc.create_icon(admin, emoji="🏮")
    record("test_20_create_emoji_icon",
           emoji_icon["emoji"] == "🏮" and emoji_icon["category"] == "grid",
           f"icon={emoji_icon}")

    # test 21: 上传图片图标(data URL 入库)
    tiny_png = ("data:image/png;base64,"
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42"
                "mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
    img_icon = await svc.create_icon(admin, image=tiny_png, name="测试上传")
    record("test_21_upload_image_icon",
           img_icon["url"] == tiny_png and img_icon["emoji"] == "",
           f"url_len={len(img_icon['url'])}")

    # test 22: 非法图片格式被拒
    ok, msg = await _expect_value_error(
        svc.create_icon(admin, image="data:text/html;base64,PGI+"), "data:image")
    record("test_22_invalid_image_format_rejected", ok, msg)

    # test 23: 参数缺失被拒
    ok, msg = await _expect_value_error(svc.create_icon(admin), "二者之一")
    record("test_23_icon_params_required", ok, msg)

    # ========================================================
    # 汇总
    # ========================================================
    print("\n".join(RESULTS))
    print("\n" + "=" * 60)
    print(f"  网站图标智能管理模块端到端测试: "
          f"通过 {PASS} / 失败 {FAIL} / 总计 {PASS + FAIL}")
    print("=" * 60)
    return FAIL


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    raise SystemExit(exit_code)
