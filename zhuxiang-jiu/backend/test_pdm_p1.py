"""38号·AI智能产品管理模块 P1 专项测试(AI 审图 + 智能下架建议 + 学习回流)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_pdm_p1.py

覆盖(P1, 设计文档 §7):
    1. 审图解析(7):    JSON轨判定/关键词兜底/图文一致性/轻缺陷/
                        报告口径(quality/flagged)
    2. 审图降级(4):    vision未配置规则轨/本地URL规则轨/aiSkipped/
                        疑似低清提示
    3. 审图接入上传(5): 上传自动审图/违规图flagged/重传再审/
                        销毁留痕/非flagged态拒绝
    4. 审图联动预审(2): 主图审图quality分透传product_gate
    5. 智能下架建议(4): 零销+超龄建议/AI低分建议/主图被标记建议/
                        正常品不建议
    6. 学习回流(6):    终审自动回流/幂等409/AI拒被人工翻案correct=False/
                        fast_track被驳回失误/手动补提403/未知裁决409
    7. HTTP路由(4):    reupload/destroy/listing-advice/learning-feedback
"""

import asyncio
import base64
import os

# 确保使用内存模式 + LLM 关闭(规则轨确定性)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.pdm_service import PdmService
from services.pdm_image_review_service import (
    PdmImageReviewService, parse_vision_reply,
    VIOLATION_DRINKING, VIOLATION_MINOR, VIOLATION_WATERMARK,
    VIOLATION_BLUR, VIOLATION_MISMATCH,
)
from services.perm_service import PermService
from repositories.pdm_repository import (
    PdmRepository, STATUS_DRAFT, STATUS_MANUAL_REVIEWING,
    STATUS_REJECTED, STATUS_ON_SALE, STATUS_OFF_SALE,
)
from repositories.member_repository import MemberRepository
from repositories.store import reset_store as _reset_store_impl
from repositories.ai_learning_repository import AiLearningRepository

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


_phone_seq = [200]


async def _add_member(member_repo: MemberRepository, nickname: str,
                      role: str = "member") -> int:
    _phone_seq[0] += 1
    member = await member_repo.create({
        "phone": f"138{_phone_seq[0]:08d}",
        "password": "x", "nickname": nickname, "avatar": "", "gender": 1,
        "level": 1, "growth_value": 0, "points": 0, "status": 1,
        "reg_source": "phone", "role": role,
    })
    return member["id"]


async def main():
    reset_store()
    svc = PdmService()
    review_svc = PdmImageReviewService()
    perm_svc = PermService()
    member_repo = MemberRepository()
    pdm_repo = PdmRepository()

    SUPER = 2
    operator = await _add_member(member_repo, "运营P1")
    auditor = await _add_member(member_repo, "审核P1")
    await perm_svc.assign_grant(SUPER, operator, "product.operate")
    await perm_svc.assign_grant(SUPER, auditor, "product.approve")

    # ========================================================
    # 1. 审图解析(纯函数, 注入 vision 回复)
    # ========================================================
    print("\n========== 1. 审图解析 ==========")

    r = parse_vision_reply(
        '{"drinking_action": false, "minor_in_image": false, '
        '"vulgar_content": false, "watermark": false, "blurry": false, '
        '"described_objects": "白酒礼盒静物摆拍"}',
        product_name="竹奕礼盒 500ml")
    record("JSON轨无违规", r["violations"] == []
           and r["describedObjects"] == "白酒礼盒静物摆拍",
           f"实际{r}")

    r = parse_vision_reply(
        '{"drinking_action": true, "minor_in_image": true, '
        '"vulgar_content": false, "watermark": true, "blurry": false, '
        '"described_objects": "聚餐碰杯"}')
    record("JSON轨命中饮酒+未成年+水印",
           {VIOLATION_DRINKING, VIOLATION_MINOR,
            VIOLATION_WATERMARK} <= set(r["violations"]),
           f"实际{r['violations']}")

    # 关键词兜底(模型回复非 JSON 格式)
    r = parse_vision_reply("这张图片显示有人正在干杯饮酒, 场景含未成年人。")
    record("关键词兜底命中",
           VIOLATION_DRINKING in r["violations"]
           and VIOLATION_MINOR in r["violations"],
           f"实际{r['violations']}")

    # 图文不一致: 描述与商品名零交集
    r = parse_vision_reply(
        '{"drinking_action": false, "minor_in_image": false, '
        '"vulgar_content": false, "watermark": false, "blurry": false, '
        '"described_objects": "运动鞋一只"}',
        product_name="竹奕白酒礼盒")
    record("图文不一致判定",
           VIOLATION_MISMATCH in r["violations"],
           f"实际{r['violations']}")

    # 报告口径: 硬红线归零+flagged / 轻缺陷每项扣25(两项=50)不禁用
    report = review_svc._build_report(
        "vision", [VIOLATION_WATERMARK, VIOLATION_BLUR], "", 5000)
    record("轻缺陷质量50不禁用",
           report["quality"] == 50.0 and not report["flagged"]
           and set(report["softHits"]) ==
           {VIOLATION_WATERMARK, VIOLATION_BLUR},
           f"实际{report}")
    report = review_svc._build_report(
        "vision", [VIOLATION_DRINKING], "", 5000)
    record("硬红线质量0+flagged",
           report["quality"] == 0.0 and report["flagged"]
           and VIOLATION_DRINKING in report["hardHits"],
           f"实际{report}")

    # ========================================================
    # 2. 审图降级(LLM off → 规则轨)
    # ========================================================
    print("\n========== 2. 审图降级 ==========")

    r = review_svc.review_image("/media/image/x.png", size=5000)
    record("LLM关闭规则轨放行",
           r["mode"] == "rule" and not r["flagged"]
           and not r["aiSkipped"], f"实际{r}")

    # vision 开启但调用失败(假 key 401) → 规则轨 + aiSkipped 转人工
    os.environ["LLM_API_KEY"] = "fake-key"
    os.environ["LLM_ENABLED"] = "on"
    try:
        r = review_svc.review_image("https://cdn.example.com/a.png",
                                    size=5000)
        record("vision异常降级aiSkipped",
               r["mode"] == "rule" and r["aiSkipped"], f"实际{r}")
    finally:
        os.environ.pop("LLM_API_KEY", None)
        os.environ["LLM_ENABLED"] = "off"

    # 疑似低清(字节数过小)
    r = review_svc.review_image("/media/image/tiny.png", size=100)
    record("疑似低清提示(轻缺陷)",
           VIOLATION_BLUR in r["violations"] and not r["flagged"],
           f"实际{r}")

    # 本地 URL 在 vision 开启时也走规则轨
    os.environ["LLM_API_KEY"] = "fake-key"
    os.environ["LLM_ENABLED"] = "on"
    try:
        r = review_svc.review_image("/media/image/local.png", size=5000)
        record("本地URL走规则轨",
               r["mode"] == "rule" and r["aiSkipped"], f"实际{r}")
    finally:
        os.environ.pop("LLM_API_KEY", None)
        os.environ["LLM_ENABLED"] = "off"

    # ========================================================
    # 3. 审图接入上传(flagged 流转)
    # ========================================================
    print("\n========== 3. 审图接入上传 ==========")

    img = await svc.upload_image(operator, "member",
                                 base64.b64encode(b"x" * 4096).decode(),
                                 ".png",
                                 product_name="竹香测试", category="经典系列")
    record("上传自动审图(规则轨报告)",
           (img.get("aiReview") or {}).get("mode") == "rule"
           and img["status"] == "usable", f"实际{img.get('aiReview')}")

    # 模拟 vision 判违规: 直接注入审图报告 → flagged
    await pdm_repo.update_image(img["imageId"], {
        "status": "flagged",
        "aiReview": review_svc._build_report(
            "vision", [VIOLATION_DRINKING], "聚餐碰杯", 4096)})
    record("违规图flagged",
           (await svc.get_image(img["imageId"]))["status"]
           == "flagged")

    # 重传 → 重新审图(规则轨放行) → usable
    # (内存模式 get 返回引用, 先存旧 URL 再比对)
    old_url = img["url"]
    img2 = await svc.reupload_image(
        operator, "member", img["imageId"],
        base64.b64encode(b"y" * 4096).decode(), ".png")
    record("重传再审视图回usable",
           img2["status"] == "usable"
           and img2["url"] != old_url,
           f"实际{img2['status']} {img2['url'] != old_url}")

    # 非 flagged 态拒绝重传
    ok, msg = await _expect(
        ValueError, svc.reupload_image(
            operator, "member", img["imageId"],
            base64.b64encode(b"z" * 4096).decode(), ".png"))
    record("非flagged态拒绝重传409", ok, msg)

    # 销毁流转: 先 flag 再销毁
    await pdm_repo.update_image(img["imageId"], {"status": "flagged"})
    destroyed = await svc.destroy_image(operator, "member",
                                        img["imageId"])
    record("销毁留痕destroyed",
           destroyed["status"] == "destroyed"
           and destroyed.get("destroyedBy") == operator,
           f"实际{destroyed.get('status')}")
    ok, msg = await _expect(
        ValueError, svc.destroy_image(operator, "member", img["imageId"]))
    record("非flagged态拒绝销毁409", ok, msg)

    # ========================================================
    # 4. 审图联动上架预审(imageQuality 透传)
    # ========================================================
    print("\n========== 4. 审图联动预审 ==========")

    p = await svc.create_product(operator, "member", {
        "name": "竹香P1联动品", "price": 888, "alcohol": 42,
        "description": "审图质量分联动测试。"})
    pid = p["product_id"]
    # 图库注入低质图(单项blur 轻缺陷 quality=75)并设为主图
    low_img = await svc.upload_image(
        operator, "member",
        base64.b64encode(b"a" * 4096).decode(), ".png")
    await pdm_repo.update_image(low_img["imageId"], {
        "aiReview": review_svc._build_report(
            "vision", [VIOLATION_BLUR], "", 4096)})
    await svc.update_images(operator, "member", pid, low_img["url"])
    recheck = await svc.ai_precheck(pid)
    img_factor = next((f for f in recheck["factors"]
                       if f["name"] == "image_quality"), {})
    record("低质主图质量分75透传预审",
           img_factor.get("score") == 75.0, f"实际{img_factor}")

    # ========================================================
    # 5. 智能下架建议
    # ========================================================
    print("\n========== 5. 智能下架建议 ==========")

    # 正常在售品(种子11款) → 无建议; 造一个零销+低分+旧创建日
    p = await svc.create_product(operator, "member", {
        "name": "竹香滞销品", "price": 888, "alcohol": 42,
        "description": "智能下架建议测试。"})
    stale_id = p["product_id"]
    await svc.submit_product(operator, "member", stale_id)
    await svc.review_product(auditor, "member", stale_id, True)
    # 置旧创建日 + 低 AI 分
    from repositories.product_repository import ProductRepository
    product = await ProductRepository().get_by_id(stale_id)
    product = dict(product)
    product.pop("stock", None); product.pop("reserved", None)
    product["created_at"] = "2025-01-01T00:00:00+00:00"
    await ProductRepository().save_product(product)
    await pdm_repo.update_pdm_product(stale_id, {
        "aiReview": {"score": 45.0, "action": "reject",
                     "factors": []}})

    advices = await svc.listing_advice()
    mine = next((a for a in advices
                 if a["productId"] == stale_id), None)
    record("零销+超龄+低分命中建议",
           mine is not None
           and len(mine["reasons"]) >= 2
           and mine["action"] == "delist_suggested",
           f"实际{mine}")
    normal = next((a for a in advices
                   if a["productId"] == "ZX42-2026L07"), None)
    record("正常在售品不建议", normal is None,
           f"实际命中{normal}")

    # 主图被标记建议(flagged URL 被 update_images 拦截是 P0 正确
    # 行为; 此处直接改商品 images 模拟存量绑定验证建议引擎)
    flagged_img = await svc.upload_image(
        operator, "member",
        base64.b64encode(b"b" * 4096).decode(), ".png")
    await pdm_repo.update_image(flagged_img["imageId"], {
        "status": "flagged"})
    ok, msg = await _expect(
        ValueError,
        svc.update_images(operator, "member", stale_id,
                          flagged_img["url"]))
    record("flagged图更换拦截409(P0行为回归)", ok, msg)
    product = await ProductRepository().get_by_id(stale_id)
    product = dict(product)
    product.pop("stock", None); product.pop("reserved", None)
    product["images"] = {"main": flagged_img["url"], "gallery": []}
    await ProductRepository().save_product(product)
    advices = await svc.listing_advice()
    mine = next((a for a in advices
                 if a["productId"] == stale_id), None)
    record("主图被标记触发建议",
           mine is not None
           and any("主图被" in x for x in mine["reasons"]),
           f"实际{mine}")

    # ========================================================
    # 6. 学习回流
    # ========================================================
    print("\n========== 6. 学习回流 ==========")

    learning_repo = AiLearningRepository()
    # 新品走全流程(终审自动回流)
    p3 = await svc.create_product(operator, "member", {
        "name": "竹香回流品", "price": 888, "alcohol": 42,
        "description": "学习回流测试。"})
    pid3 = p3["product_id"]
    await svc.submit_product(operator, "member", pid3)
    await svc.review_product(auditor, "member", pid3, True)
    feedbacks = await learning_repo.list_feedback("product_gate",
                                                  limit=10)
    record("终审自动回流feedback",
           len(feedbacks) >= 1
           and feedbacks[0].get("scorerId") == "product_gate"
           and feedbacks[0].get("actualAction") == "approve",
           f"实际{len(feedbacks)}条")
    overlay = await pdm_repo.get_pdm_product(pid3)
    record("learningFed幂等标记",
           overlay.get("learningFed") is True
           and overlay.get("learningDecision") == "approve",
           f"实际{overlay.get('learningFed')}")

    # 重复回流 → 409
    ok, msg = await _expect(
        ValueError, svc.submit_learning_feedback(pid3, "approve"))
    record("重复回流409幂等", ok, msg)

    # AI拒被人工翻案: rejected 品手动回流 approve → correct=False
    p4 = await svc.create_product(operator, "member", {
        "name": "竹香翻案品", "price": 3000, "alcohol": 53,
        "description": "开怀畅饮。"})  # AI 拒
    pid4 = p4["product_id"]
    p4 = await svc.submit_product(operator, "member", pid4)
    # 未知裁决 → 409
    ok, msg = await _expect(
        ValueError, svc.submit_learning_feedback(pid4, "翻案"))
    record("未知裁决409", ok, msg)
    result = await svc.submit_learning_feedback(pid4, "approve")
    record("AI拒被翻案correct=False",
           result.get("correct") is False,
           f"实际{result.get('correct')}")

    # fast_track 被人工驳回 → correct=False(快车道失误)
    p5 = await svc.create_product(operator, "member", {
        "name": "竹香快车道品", "price": 888, "alcohol": 42,
        "description": "快车道失误测试。"})
    pid5 = p5["product_id"]
    await svc.submit_product(operator, "member", pid5)
    result = await svc.submit_learning_feedback(pid5, "reject")
    record("fast_track被驳回correct=False",
           result.get("correct") is False,
           f"实际{result.get('correct')}")

    # 无预审快照 → 404
    p6 = await svc.create_product(operator, "member", {
        "name": "竹香无快照品", "price": 888, "alcohol": 42,
        "description": "无快照回流测试。"})
    ok, msg = await _expect(
        KeyError, svc.submit_learning_feedback(
            p6["product_id"], "approve"))
    record("无AI快照回流404", ok, msg)

    # ========================================================
    # 7. HTTP 路由
    # ========================================================
    print("\n========== 7. HTTP路由 ==========")

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    headers = {"X-Member-Id": str(SUPER), "X-Role": "admin"}

    r = client.post("/api/pdm/images/999/destroy", headers=headers)
    record("HTTP销毁未知图片404", r.status_code == 404,
           f"实际{r.status_code}")

    r = client.get("/api/pdm/listing-advice", headers=headers)
    body = r.json()
    record("HTTP下架建议200",
           r.status_code == 200 and body["success"] is True
           and isinstance(body["data"], list),
           f"实际{r.status_code}")

    r = client.post(
        f"/api/pdm/products/{pid5}/learning-feedback",
        json={"decision": "reject"}, headers=headers)
    record("HTTP重复回流409", r.status_code == 409,
           f"实际{r.status_code}")

    r = client.post("/api/pdm/images",
                    json={"dataBase64": base64.b64encode(
                        b"http" * 1024).decode(), "ext": ".png",
                        "productName": "竹香HTTP", "category": "经典"},
                    headers=headers)
    body = r.json()
    record("HTTP上传带审图200",
           r.status_code == 200 and body["success"] is True
           and body["data"]["aiReview"]["mode"] == "rule",
           f"实际{r.status_code}")

    print("\n" + "=" * 62)
    for line in RESULTS:
        print(line)
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()) and 1 or 0)
