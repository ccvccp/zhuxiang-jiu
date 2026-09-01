"""38号·AI智能产品管理模块 P2 专项测试(AI 设计工坊 + 看板)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_pdm_p2.py

覆盖(P2, 设计文档 §7):
    1. 主图生成(5):   规则模板轨prompt/生成URL管线/入库+审图一站式/
                        生成图标记/未知商品404
    2. 文案优化(5):   规则轨三字段/禁用词扫描/仅建议不入库(applied=False)/
                        LLM轨JSON解析/未知商品404
    3. 主图A/B建议(5): 版本不足提示/有数据背书保留/无数据人工投放/
                        小流量建议/候选主图去重
    4. HTTP路由(4):   generate-main-image/copy-optimize/main-image-ab/
                        越权403
    5. 前端看板(2):   html+js 文件就位/关键区块与端点引用
"""

import asyncio
import os

# 确保使用内存模式 + LLM 关闭(规则轨确定性)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.pdm_service import PdmService
from services.pdm_design_service import (
    PdmDesignService, scan_banned_words, _build_image_prompt_url,
    TRACK_RULE,
)
from services.perm_service import PermService
from repositories.pdm_repository import (
    PdmRepository, STATUS_ON_SALE,
)
from repositories.member_repository import MemberRepository
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


async def _expect(exc_type, coro, keyword=""):
    try:
        await coro
        return False, ""
    except exc_type as exc:
        return (not keyword or keyword in str(exc)), str(exc)
    except Exception as exc:
        return False, f"非预期异常 {type(exc).__name__}: {exc}"


_phone_seq = [300]


async def _add_member(member_repo: MemberRepository, nickname: str,
                      role: str = "member") -> int:
    _phone_seq[0] += 1
    member = await member_repo.create({
        "phone": f"137{_phone_seq[0]:08d}",
        "password": "x", "nickname": nickname, "avatar": "", "gender": 1,
        "level": 1, "growth_value": 0, "points": 0, "status": 1,
        "reg_source": "phone", "role": role,
    })
    return member["id"]


async def main():
    reset_store()
    svc = PdmService()
    design_svc = PdmDesignService()
    perm_svc = PermService()
    member_repo = MemberRepository()
    pdm_repo = PdmRepository()

    SUPER = 2
    operator = await _add_member(member_repo, "运营P2")
    auditor = await _add_member(member_repo, "审核P2")
    passerby = await _add_member(member_repo, "路人P2")
    await perm_svc.assign_grant(SUPER, operator, "product.operate")
    await perm_svc.assign_grant(SUPER, operator, "product.view")
    await perm_svc.assign_grant(SUPER, auditor, "product.approve")

    # ========================================================
    # 1. AI 主图生成
    # ========================================================
    print("\n========== 1. 主图生成 ==========")

    p = await svc.create_product(operator, "member", {
        "name": "竹香P2设计品", "price": 888, "alcohol": 42,
        "description": "AI 设计工坊测试。", "scenes": ["商务宴请"]})
    pid = p["product_id"]

    result = await svc.generate_main_image(operator, "member", pid)
    design = result["design"]
    img = result["image"]
    record("规则模板轨prompt(LLM off)",
           design["track"] == TRACK_RULE
           and "竹" in design["prompt"] and len(design["prompt"]) > 20,
           f"实际{design['track']} {design['prompt'][:40]}")
    record("生成URL走text_to_image管线",
           "text_to_image" in img["url"]
           and "prompt=" in img["url"], f"实际{img['url'][:60]}")
    record("入库+审图一站式",
           img["imageId"] > 0
           and (img.get("aiReview") or {}).get("mode") == "rule",
           f"实际{img.get('aiReview')}")
    record("生成图标记generated+prompt留痕",
           img.get("generated") is True
           and img.get("designPrompt") == design["prompt"],
           f"实际generated={img.get('generated')}")

    ok, msg = await _expect(
        KeyError, svc.generate_main_image(operator, "member", "PD-NOPE"))
    record("未知商品生成404", ok, msg)

    # 直接构造 URL 工具函数
    url = _build_image_prompt_url("测试 prompt")
    record("URL构造prompt编码", "prompt=%E6%B5%8B%E8%AF%95" in url,
           f"实际{url[:80]}")

    # ========================================================
    # 2. AI 文案优化
    # ========================================================
    print("\n========== 2. 文案优化 ==========")

    copy_result = await svc.optimize_copy(operator, "member", pid)
    record("规则轨三字段",
           copy_result["track"] == TRACK_RULE
           and copy_result["title"] and copy_result["description"]
           and "竹香" in copy_result["description"],
           f"实际{copy_result}")
    record("仅建议不入库(applied=False)",
           copy_result["applied"] is False
           and copy_result["bannedHits"] == [],
           f"实际{copy_result.get('applied')}")

    # 禁用词扫描(36号共享口径)
    hits = scan_banned_words("最好的酒, 开怀畅饮")
    record("禁用词扫描命中",
           "最好" in hits and "开怀畅饮" in hits, f"实际{hits}")
    record("禁用词扫描无命中", scan_banned_words("绵柔顺喉") == [])

    # LLM 轨 JSON 解析(注入回复)
    data = design_svc._extract_json(
        '前置文本 {"title": "竹香优化标题", "subtitle": "副标题",'
        ' "description": "优化描述"} 后置文本')
    record("LLM回复JSON提取(容忍前后缀)",
           data and data["title"] == "竹香优化标题", f"实际{data}")

    ok, msg = await _expect(
        KeyError, svc.optimize_copy(operator, "member", "PD-NOPE"))
    record("未知商品文案404", ok, msg)

    # ========================================================
    # 3. 主图 A/B 建议
    # ========================================================
    print("\n========== 3. 主图A/B建议 ==========")

    # 版本不足(新品仅1版)
    advice = await svc.main_image_ab_advice(operator, "member", pid)
    record("版本不足提示",
           advice["sufficient"] is False
           and advice["recommendation"] == "collect_more_versions",
           f"实际{advice}")

    # 造多主图版本 + 销量数据: 走全流程
    await svc.submit_product(operator, "member", pid)
    await svc.review_product(auditor, "member", pid, True)
    await svc.update_images(operator, "member", pid,
                            "/media/image/second-main.png")
    # 注入销量/评分(直接改商品主数据)
    from repositories.product_repository import ProductRepository
    product = await ProductRepository().get_by_id(pid)
    product = dict(product)
    product.pop("stock", None); product.pop("reserved", None)
    product["sales_total"] = 120
    product["rating_avg"] = 4.8
    await ProductRepository().save_product(product)

    advice = await svc.main_image_ab_advice(operator, "member", pid)
    record("数据背书建议保留当前",
           advice["sufficient"] is True
           and advice["recommendation"] == "keep_current"
           and advice["salesTotal"] == 120,
           f"实际{advice.get('recommendation')}")

    # 无销量 → 人工 A/B
    product = await ProductRepository().get_by_id(pid)
    product = dict(product)
    product.pop("stock", None); product.pop("reserved", None)
    product["sales_total"] = 0
    await ProductRepository().save_product(product)
    advice = await svc.main_image_ab_advice(operator, "member", pid)
    record("无数据人工投放建议",
           advice["recommendation"] == "manual_ab",
           f"实际{advice.get('recommendation')}")

    # 候选主图去重(多版本同主图只取一张)
    candidates = advice.get("candidates") or []
    mains = [c["main"] for c in candidates]
    record("候选主图去重",
           len(mains) == len(set(mains)) and len(mains) >= 2,
           f"实际{mains}")

    # ========================================================
    # 4. HTTP 路由
    # ========================================================
    print("\n========== 4. HTTP路由 ==========")

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    admin_headers = {"X-Member-Id": str(SUPER), "X-Role": "admin"}

    r = client.post(
        f"/api/pdm/products/{pid}/design/generate-main-image",
        headers=admin_headers)
    body = r.json()
    record("HTTP生成主图200",
           r.status_code == 200 and body["success"] is True
           and body["data"]["image"]["generated"] is True,
           f"实际{r.status_code}")

    r = client.post(
        f"/api/pdm/products/{pid}/design/copy-optimize",
        headers=admin_headers)
    record("HTTP文案优化200",
           r.status_code == 200
           and r.json()["data"]["applied"] is False,
           f"实际{r.status_code}")

    r = client.get(f"/api/pdm/products/{pid}/design/main-image-ab",
                   headers=admin_headers)
    record("HTTP主图AB建议200",
           r.status_code == 200
           and r.json()["data"]["sufficient"] is True,
           f"实际{r.status_code}")

    r = client.post(
        f"/api/pdm/products/{pid}/design/generate-main-image",
        headers={"X-Member-Id": str(passerby), "X-Role": "member"})
    record("HTTP路人生成越权403", r.status_code == 403,
           f"实际{r.status_code}")

    # ========================================================
    # 5. 前端看板
    # ========================================================
    print("\n========== 5. 前端看板 ==========")

    html_path = os.path.join(os.path.dirname(__file__), "..",
                             "ai-pdm-dashboard.html")
    js_path = os.path.join(os.path.dirname(__file__), "..",
                           "js", "pdm-dashboard.js")
    record("看板html+js文件就位",
           os.path.exists(html_path) and os.path.exists(js_path))
    with open(html_path, encoding="utf-8") as f:
        html = f.read()
    blocks = ["全景统计", "商品管理", "待审队列", "版本与回滚",
              "图片中心", "AI 设计工坊", "智能下架建议"]
    record("看板七区块齐全",
           all(b in html for b in blocks),
           f"缺{[b for b in blocks if b not in html]}")
    with open(js_path, encoding="utf-8") as f:
        js = f.read()
    endpoints = ["/api/pdm/report/overview", "/api/pdm/products",
                 "/api/pdm/reviews/pending", "/api/pdm/images",
                 "/api/pdm/listing-advice",
                 "design/generate-main-image", "design/copy-optimize",
                 "design/main-image-ab"]
    record("看板端点引用齐全",
           all(e in js for e in endpoints),
           f"缺{[e for e in endpoints if e not in js]}")

    print("\n" + "=" * 62)
    for line in RESULTS:
        print(line)
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()) and 1 or 0)
