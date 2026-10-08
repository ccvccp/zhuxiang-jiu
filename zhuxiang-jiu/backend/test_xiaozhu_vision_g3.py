"""G3 商品图理解: 产品页视觉问答 fail-soft 一致性测试

小竹Agent方案甄别后真缺口 G3(用户提级首位):
产品页问外观类问题 → page_context 随轮上报 → 主图
glm-4v 理解注入回复。本套验证: 触发条件门控 / 视觉轨
mock 全链 / 失败回退 chat 原轨零回归 / 80 字上限。
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "on"

PASS = 0
FAIL = 0


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


async def main():
    from services.xiaozhu_service import XiaozhuService
    svc = XiaozhuService()

    print("[01 触发条件门控(未命中一律 None)]")
    r = await svc._vision_product_qa(
        "这酒什么颜色", None, {"sessionId": 1, "memberId": 1})
    record("无 page_context → None", r is None)
    r = await svc._vision_product_qa(
        "这酒什么颜色", {"page": "index"}, {"sessionId": 1})
    record("page 非 product-detail → None", r is None)
    r = await svc._vision_product_qa(
        "这酒什么颜色", {"page": "product-detail"},
        {"sessionId": 1})
    record("无 productId → None", r is None)
    r = await svc._vision_product_qa(
        "多少钱", {"page": "product-detail", "productId": "68"},
        {"sessionId": 1})
    record("非视觉词(多少钱) → None", r is None)
    r = await svc._vision_product_qa(
        "这酒什么颜色",
        {"page": "product-detail", "productId": "404"},
        {"sessionId": 1})
    record("商品不存在 → None(本地无该数据, fail-soft)",
           r is None)

    print("[02 视觉轨 mock 全链(命中应答)]")
    from repositories.product_repository import (
        ProductRepository,
    )
    _orig_get = ProductRepository.get_by_id

    async def _fake_get(self, pid):
        return {"id": pid, "name": "竹香液·测试款",
                "images": {"main":
                           "https://example.com/p.jpg"}}

    ProductRepository.get_by_id = _fake_get
    import services.llm_client as _llm_mod
    _orig_vision = _llm_mod.provider_client.vision
    _seen = {}

    def _fake_vision(prompt, url, media_type="image"):
        _seen["prompt"] = prompt
        _seen["url"] = url
        return ("这款是白瓷瓶身、红色瓶盖，"
                "标签印有「竹香液」字样，礼盒金色包装。")

    _llm_mod.provider_client.vision = _fake_vision
    r = await svc._vision_product_qa(
        "这酒什么颜色",
        {"page": "product-detail", "productId": "68"},
        {"sessionId": 1, "memberId": 1})
    record("命中视觉词+图存在 → dict",
           isinstance(r, dict) and bool(r.get("reply")),
           str(r))
    record("vision 收到主图 URL",
           _seen.get("url") == "https://example.com/p.jpg",
           str(_seen.get("url")))
    record("prompt 含商品名+问题",
           "竹香液·测试款" in (_seen.get("prompt") or "")
           and "什么颜色" in (_seen.get("prompt") or ""))
    record("card 恒 None(纯对话)", (r or {}).get("card") is None)

    print("[03 视觉轨 80 字上限 + 空回退]")
    def _long_vision(prompt, url, media_type="image"):
        return "瓶" * 200

    _llm_mod.provider_client.vision = _long_vision
    r = await svc._vision_product_qa(
        "瓶身什么样子",
        {"page": "product-detail", "productId": "68"},
        {"sessionId": 1})
    record("超长视觉回复截断 ≤80 字",
           r is not None and len(r["reply"]) <= 80,
           f"len={len((r or {}).get('reply') or '')}")

    def _empty_vision(prompt, url, media_type="image"):
        return None

    _llm_mod.provider_client.vision = _empty_vision
    r = await svc._vision_product_qa(
        "瓶身什么样子",
        {"page": "product-detail", "productId": "68"},
        {"sessionId": 1})
    record("vision 失败 → None(回退 chat 原轨)", r is None)

    print("[04 chat 分支集成: 视觉失败零回归]")
    _orig_intent = svc._llm_dialog_intent

    async def _fake_chat(session, text):
        return {"intent": "chat", "reply": "这款酒很受欢迎呢"}

    svc._llm_dialog_intent = _fake_chat
    sid = (await svc.open_session(1, "voice"))["sessionId"]
    r = await svc.handle_text(
        sid, "小竹，这酒什么颜色",
        page_context={"page": "product-detail",
                      "productId": "68"})
    record("vision 失败回退 chat 原轨",
           r.get("reply") == "这款酒很受欢迎呢"
           and r.get("track") == "llm_dialog",
           f"reply={r.get('reply')!r} track={r.get('track')}")

    print("[05 chat 分支集成: 视觉成功短路]")
    _llm_mod.provider_client.vision = _fake_vision
    r = await svc.handle_text(
        sid, "小竹，瓶身什么图案",
        page_context={"page": "product-detail",
                      "productId": "68"})
    _turn = r.get("turn") or {}
    record("视觉轨应答(track=vision_dialog)",
           r.get("track") == "vision_dialog"
           and "白瓷瓶" in str(r.get("reply")),
           f"reply={r.get('reply')!r} track={r.get('track')}")
    record("turn 留痕 visionUsed",
           bool(_turn.get("visionUsed")) is True,
           str(_turn.get("visionUsed")))
    await svc.delete_session(sid)

    print("[06 无 page_context 老链路零影响]")
    sid2 = (await svc.open_session(1, "voice"))["sessionId"]
    r = await svc.handle_text(sid2, "小竹，看新品")
    record("普通推荐轮不带视觉",
           (r.get("card") or {}).get("type") == "product_list",
           str((r.get("card") or {}).get("type")))
    await svc.delete_session(sid2)

    # 还原 mock
    ProductRepository.get_by_id = _orig_get
    _llm_mod.provider_client.vision = _orig_vision
    svc._llm_dialog_intent = _orig_intent

    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
