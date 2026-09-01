"""38号·AI智能产品管理模块 P0 专项测试(Service 层直调 + HTTP 路由)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_pdm_p0.py

覆盖(P0 核心闭环, 设计文档 §7):
    1. 权限矩阵(6):      无权拒绝/JWT角色回退/perm授权/越权拦截/授权过期
    2. 创建与AI预审(7):  草稿不可见/流转快车道/60-79人工/违禁词拒/重复提交
    3. 人工终审与SoD(5): 通过上架/驳回/SoD自审拦截/待审队列/手动复评
    4. 上下架(7):        下架原因必填/幂等/重新上架/防绕审/紧急下架/越权
    5. 编辑双轨(4):      substantive回落重审/cosmetic不动状态/审核中禁编辑
    6. 版本回滚(3):      版本列表/回滚须重审/未知版本404
    7. 图片中心(5):      上传图库/在售换图回落/标记图禁用/图片回滚
    8. 消费端联动(2):    管理态商品不可见/在售可见
    9. 看板与审计(3):    overview结构/模块流水/perm审计双写
    10. HTTP路由(5):     401/403/200创建/200看板/404
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
from services.perm_service import PermService
from repositories.pdm_repository import (
    PdmRepository, STATUS_DRAFT, STATUS_AI_REVIEWING,
    STATUS_MANUAL_REVIEWING, STATUS_REJECTED, STATUS_ON_SALE,
    STATUS_OFF_SALE,
)
from repositories.perm_repository import PermRepository
from repositories.product_repository import ProductRepository
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


_phone_seq = [100]


async def _add_member(member_repo: MemberRepository, nickname: str,
                      role: str = "member") -> int:
    _phone_seq[0] += 1
    member = await member_repo.create({
        "phone": f"139{_phone_seq[0]:08d}",
        "password": "x", "nickname": nickname, "avatar": "", "gender": 1,
        "level": 1, "growth_value": 0, "points": 0, "status": 1,
        "reg_source": "phone", "role": role,
    })
    return member["id"]


async def main():
    reset_store()
    svc = PdmService()
    perm_svc = PermService()
    member_repo = MemberRepository()
    product_repo = ProductRepository()
    perm_repo = PermRepository()
    pdm_repo = PdmRepository()

    # 种子会员2为 admin(SUPER); 造运营/审核员并授产品域权限
    SUPER = 2
    operator = await _add_member(member_repo, "商品运营甲")
    auditor = await _add_member(member_repo, "商品审核员乙")
    passerby = await _add_member(member_repo, "路人会员")
    await perm_svc.assign_grant(SUPER, operator, "product.operate")
    await perm_svc.assign_grant(SUPER, auditor, "product.approve")

    # ========================================================
    # 1. 权限矩阵
    # ========================================================
    print("\n========== 1. 权限矩阵 ==========")

    ok, msg = await _expect(
        PermissionError,
        svc.create_product(passerby, "member",
                           {"name": "越权商品", "price": 100}))
    record("无授权无角色拒绝403", ok, msg)

    path = await svc.check_permission(SUPER, "admin", "manage")
    record("JWT admin角色回退", path["via"] == "jwt_role", f"实际{path}")

    path2 = await svc.check_permission(operator, "member", "operate")
    record("perm授权product.operate",
           path2["via"] == "perm_grant" and path2.get("grantId"),
           f"实际{path2}")

    ok, msg = await _expect(
        PermissionError,
        svc.create_product(auditor, "member",
                           {"name": "审核员建品", "price": 100}))
    record("审核员(product.approve)不可建品", ok, msg)

    # 授权过期: 直改 expiresAt 为过去
    grants = await perm_repo.list_grants(member_id=operator,
                                         node_code="product.operate",
                                         status="active")
    await perm_repo.update_grant(grants[0]["grantId"], {
        "expiresAt": "2000-01-01T00:00:00+00:00"})
    ok, msg = await _expect(
        PermissionError,
        svc.check_permission(operator, "member", "operate"))
    record("授权过期惰性失效", ok, msg)
    await perm_repo.update_grant(grants[0]["grantId"], {
        "expiresAt": "2099-01-01T00:00:00+00:00"})

    # ========================================================
    # 2. 创建与 AI 预审流转
    # ========================================================
    print("\n========== 2. 创建与AI预审 ==========")

    p1 = await svc.create_product(operator, "member", {
        "name": "竹香测试·42°经典", "subtitle": "测试商品",
        "price": 300, "stock": 10, "alcohol": 42,
        "description": "竹香型白酒, 固态发酵。",
    })
    pid1 = p1["product_id"]
    record("创建草稿draft+v1",
           p1["pdmStatus"] == STATUS_DRAFT
           and p1["currentVersion"] == 1, f"实际{p1['pdmStatus']}")
    visible = [p for p in await product_repo.list_products()
               if p["product_id"] == pid1]
    record("草稿消费端不可见", not visible)

    # 非法流转: draft 直接终审 → 409
    ok, msg = await _expect(
        ValueError,
        svc.review_product(auditor, "member", pid1, True))
    record("draft直接终审409", ok, msg)

    p1 = await svc.submit_product(operator, "member", pid1)
    ai = p1.get("aiReview") or {}
    record("提交AI预审满分快车道",
           p1["pdmStatus"] == STATUS_MANUAL_REVIEWING
           and ai.get("action") == "fast_track"
           and ai.get("score", 0) >= 80,
           f"状态{p1['pdmStatus']} ai={ai.get('score')}")

    # 在审中重复提交 → 409
    ok, msg = await _expect(
        ValueError,
        svc.submit_product(operator, "member", pid1))
    record("在审中重复提交409", ok, msg)

    # 60-79 人工重点审(单极限词 → compliance=50; 价格取中位区间)
    p2 = await svc.create_product(operator, "member", {
        "name": "竹香测试·极限词", "price": 888, "alcohol": 42,
        "description": "顶级的测试酒。",
    })
    p2 = await svc.submit_product(operator, "member", p2["product_id"])
    ai2 = p2.get("aiReview") or {}
    record("极限词落60-79人工审",
           p2["pdmStatus"] == STATUS_MANUAL_REVIEWING
           and ai2.get("action") == "manual_review"
           and 60 <= ai2.get("score", 0) < 80,
           f"状态{p2['pdmStatus']} ai={ai2.get('score')}")

    # 违禁饮酒动作词 + 价格离群 → AI 拒
    p3 = await svc.create_product(operator, "member", {
        "name": "竹香测试·违规", "price": 3000, "alcohol": 53,
        "description": "开怀畅饮, 不醉不归。",
    })
    p3 = await svc.submit_product(operator, "member", p3["product_id"])
    record("饮酒动作词+价格离群AI拒",
           p3["pdmStatus"] == STATUS_REJECTED
           and (p3.get("aiReview") or {}).get("action") == "reject",
           f"状态{p3['pdmStatus']}")
    record("驳回理由留痕", bool(p3.get("rejectReason")),
           f"实际{p3.get('rejectReason')}")

    # 手动 AI 复评(不改状态)
    recheck = await svc.ai_precheck(p3["product_id"])
    record("手动复评返回报告",
           recheck.get("scorer") == "product_gate"
           and p3["pdmStatus"] == STATUS_REJECTED,
           f"实际{recheck.get('scorer')}")

    # ========================================================
    # 3. 人工终审与 SoD
    # ========================================================
    print("\n========== 3. 人工终审与SoD ==========")

    pending = await svc.list_reviews_pending()
    record("待审队列含两品",
           len(pending) == 2
           and {p["product_id"] for p in pending}
           == {pid1, p2["product_id"]},
           f"实际{[p['product_id'] for p in pending]}")

    # SoD: admin 建品并自审 → 409(admin JWT 兜底双权限)
    pa = await svc.create_product(SUPER, "admin", {
        "name": "管理员直建品", "price": 300, "alcohol": 42,
        "description": "管理员建品自审测试。"})
    await svc.submit_product(SUPER, "admin", pa["product_id"])
    ok, msg = await _expect(
        ValueError, svc.review_product(SUPER, "admin",
                                       pa["product_id"], True),
        "SoD")
    record("SoD自审拦截(编辑≠审核)", ok, msg)

    # auditor 终审通过 → on_sale + 消费端可见
    p1 = await svc.review_product(auditor, "member", pid1, True)
    record("终审通过on_sale", p1["pdmStatus"] == STATUS_ON_SALE,
           f"实际{p1['pdmStatus']}")
    visible = [p for p in await product_repo.list_products()
               if p["product_id"] == pid1]
    record("在售消费端可见", len(visible) == 1)

    # auditor 终审驳回 p2 → rejected
    p2 = await svc.review_product(auditor, "member",
                                  p2["product_id"], False,
                                  note="极限词须修改")
    record("终审驳回+理由",
           p2["pdmStatus"] == STATUS_REJECTED
           and "极限词" in p2.get("rejectReason", ""),
           f"实际{p2.get('rejectReason')}")

    # 审核员不可执行运营操作(下架)
    ok, msg = await _expect(
        PermissionError,
        svc.take_off_sale(auditor, "member", pid1, "越权"))
    record("审核员不可下架(越权403)", ok, msg)

    # ========================================================
    # 4. 上下架
    # ========================================================
    print("\n========== 4. 上下架 ==========")

    ok, msg = await _expect(
        ValueError, svc.take_off_sale(operator, "member", pid1, ""))
    record("下架原因必填409", ok, msg)

    p1 = await svc.take_off_sale(operator, "member", pid1, "例行下架")
    record("下架off_sale+消费端不可见",
           p1["pdmStatus"] == STATUS_OFF_SALE
           and not [p for p in await product_repo.list_products()
                    if p["product_id"] == pid1],
           f"实际{p1['pdmStatus']}")

    p1b = await svc.take_off_sale(operator, "member", pid1, "重复下架")
    record("下架幂等", p1b["pdmStatus"] == STATUS_OFF_SALE
           and p1b["currentVersion"] == p1["currentVersion"])

    p1 = await svc.put_on_sale(operator, "member", pid1)
    record("重新上架on_sale", p1["pdmStatus"] == STATUS_ON_SALE,
           f"实际{p1['pdmStatus']}")
    p1b = await svc.put_on_sale(operator, "member", pid1)
    record("上架幂等", p1b["pdmStatus"] == STATUS_ON_SALE
           and p1b["currentVersion"] == p1["currentVersion"])

    # 防绕审: manual_reviewing 不可直上(管理员建品在审中)
    ok, msg = await _expect(
        ValueError,
        svc.put_on_sale(SUPER, "admin", pa["product_id"]))
    record("manual_reviewing防绕审409", ok, msg)

    # draft 直通上架: 新草制品; operator(operate)不足 → PermissionError
    pd = await svc.create_product(operator, "member", {
        "name": "竹香测试·直通品", "price": 888, "alcohol": 42,
        "description": "admin 直通上架测试。"})
    ok, msg = await _expect(
        PermissionError,
        svc.put_on_sale(operator, "member", pd["product_id"]))
    record("draft直通须manage(运营403)", ok, msg)
    # admin manage 直通
    pd = await svc.put_on_sale(SUPER, "admin", pd["product_id"])
    record("admin直通上架draft", pd["pdmStatus"] == STATUS_ON_SALE,
           f"实际{pd['pdmStatus']}")

    # 紧急下架: rejected 态 p3 任意态直达(operate 无 manage → 403)
    ok, msg = await _expect(
        PermissionError,
        svc.force_delist(operator, "member", p3["product_id"], "舆情"))
    record("紧急下架运营越权403", ok, msg)
    p3 = await svc.force_delist(SUPER, "admin", p3["product_id"],
                                "负面舆情紧急下架")
    record("紧急下架任意态直达(rejected→off_sale)",
           p3["pdmStatus"] == STATUS_OFF_SALE, f"实际{p3['pdmStatus']}")

    # ========================================================
    # 5. 编辑双轨(cosmetic / substantive)
    # ========================================================
    print("\n========== 5. 编辑双轨 ==========")

    # cosmetic: on_sale 微调描述 → 状态不动
    p1 = await svc.update_product(operator, "member", pid1,
                                  {"description": "微调描述文案。"})
    record("cosmetic微调不动在售态",
           p1["pdmStatus"] == STATUS_ON_SALE
           and p1["currentVersion"] >= 2, f"实际{p1['pdmStatus']}")

    # substantive: on_sale 改价 → 回落 draft 重审
    p1 = await svc.update_product(operator, "member", pid1,
                                  {"price": 350})
    record("substantive改价回落draft",
           p1["pdmStatus"] == STATUS_DRAFT
           and p1["price"] == 350
           and p1["member_price"] == round(350 * 0.9, 2),
           f"实际{p1['pdmStatus']}/{p1['price']}")
    record("改价后消费端不可见",
           not [p for p in await product_repo.list_products()
                if p["product_id"] == pid1])

    # 审核中禁编辑
    await svc.submit_product(operator, "member", pid1)
    ok, msg = await _expect(
        ValueError,
        svc.update_product(operator, "member", pid1,
                           {"description": "审核中改"}))
    record("审核中禁编辑409", ok, msg)
    # 清理: 驳回回 draft
    await svc.review_product(auditor, "member", pid1, False, "流程清理")

    # ========================================================
    # 6. 版本快照与回滚
    # ========================================================
    print("\n========== 6. 版本与回滚 ==========")

    versions = await svc.list_versions(pid1)
    record("版本列表倒序含变更类型",
           len(versions) >= 3
           and versions[0]["version"] > versions[-1]["version"]
           and {v["changeType"] for v in versions}
           >= {"cosmetic", "substantive"},
           f"实际{len(versions)}版")

    # 回滚到 v2(cosmetic 版本快照) → substantive 回 draft
    # (rejected 态回滚保持 rejected, 须重新提交过审——不可直上)
    p1 = await svc.rollback_version(operator, "member", pid1, 2)
    record("版本回滚须重审(非在售+新版本)",
           p1["pdmStatus"] in (STATUS_DRAFT, STATUS_REJECTED)
           and p1["currentVersion"] > versions[0]["version"],
           f"实际{p1['pdmStatus']}")

    ok, msg = await _expect(
        KeyError, svc.rollback_version(operator, "member", pid1, 999))
    record("未知版本回滚404", ok, msg)

    # ========================================================
    # 7. 图片中心
    # ========================================================
    print("\n========== 7. 图片中心 ==========")

    img = await svc.upload_image(operator, "member",
                                 base64.b64encode(b"fake-png-bytes").decode(),
                                 ".png")
    record("上传图片入库usable",
           img["status"] == "usable"
           and img["url"].startswith("/media/image/"),
           f"实际{img}")

    # 在售商品换图 → substantive 回落 draft
    pf = await svc.create_product(operator, "member", {
        "name": "竹香测试·换图品", "price": 300, "alcohol": 42,
        "description": "换图测试。"})
    await svc.submit_product(operator, "member", pf["product_id"])
    pf = await svc.review_product(auditor, "member",
                                  pf["product_id"], True)
    pf = await svc.update_images(operator, "member",
                                 pf["product_id"], img["url"],
                                 gallery=[])
    record("在售换图回落draft",
           pf["pdmStatus"] == STATUS_DRAFT
           and pf["images"]["main"] == img["url"],
           f"实际{pf['pdmStatus']}")

    # 标记(flagged)图片禁止使用
    await pdm_repo.update_image(img["imageId"], {"status": "flagged"})
    ok, msg = await _expect(
        ValueError,
        svc.update_images(operator, "member", pf["product_id"],
                          img["url"]))
    record("标记图片禁用409", ok, msg)
    await pdm_repo.update_image(img["imageId"], {"status": "usable"})

    # 图片回滚: 取 v1 快照的 images
    pf = await svc.rollback_images(operator, "member",
                                   pf["product_id"], 1)
    record("图片组回滚须重审",
           pf["pdmStatus"] == STATUS_DRAFT
           and pf["images"]["main"] != img["url"],
           f"实际{pf['images']['main'][:40]}")

    flagged = await svc.list_images(status="flagged")
    record("图库筛选flagged", isinstance(flagged, list))

    # ========================================================
    # 8. 看板与审计
    # ========================================================
    print("\n========== 8. 看板与审计 ==========")

    overview = await svc.overview()
    record("overview结构齐全",
           set(overview["statusCounts"]) ==
           {STATUS_DRAFT, STATUS_AI_REVIEWING,
            STATUS_MANUAL_REVIEWING, STATUS_REJECTED,
            STATUS_ON_SALE, STATUS_OFF_SALE}
           and "aiStats" in overview and "images" in overview
           and overview["today"]["audits"] > 0,
           f"实际{overview['statusCounts']}")

    audits = await pdm_repo.list_audits(product_id=pid1)
    actions = {a["action"] for a in audits}
    record("模块流水全动作留痕",
           {"create", "submit", "ai_precheck", "review_approve",
            "delist", "list", "update_cosmetic", "update_substantive",
            "rollback"} <= actions,
           f"实际{sorted(actions)}")

    perm_logs = await perm_repo.list_logs(limit=200)
    pdm_logs = [l for l in perm_logs
                if str(l.get("action", "")).startswith("pdm_")]
    record("perm审计双写", len(pdm_logs) >= 10,
           f"实际{len(pdm_logs)}条")

    # ========================================================
    # 9. HTTP 路由
    # ========================================================
    print("\n========== 9. HTTP路由 ==========")

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    r = client.post("/api/pdm/products",
                    json={"name": "无头请求", "price": 100})
    record("HTTP无登录401", r.status_code == 401, f"实际{r.status_code}")

    r = client.post("/api/pdm/products",
                    json={"name": "路人建品", "price": 100},
                    headers={"X-Member-Id": str(passerby),
                             "X-Role": "member"})
    record("HTTP无权限403", r.status_code == 403, f"实际{r.status_code}")

    r = client.post("/api/pdm/products",
                    json={"name": "竹香HTTP测试", "price": 300,
                          "alcohol": 42, "description": "HTTP建品。"},
                    headers={"X-Member-Id": str(SUPER),
                             "X-Role": "admin"})
    body = r.json()
    record("HTTP admin创建200",
           r.status_code == 200 and body["success"] is True
           and body["data"]["pdmStatus"] == STATUS_DRAFT,
           f"实际{r.status_code} {str(body)[:120]}")

    r = client.get("/api/pdm/report/overview",
                   headers={"X-Member-Id": str(SUPER),
                            "X-Role": "admin"})
    record("HTTP overview200",
           r.status_code == 200 and r.json()["success"] is True,
           f"实际{r.status_code}")

    r = client.get("/api/pdm/products/PD-NOPE",
                   headers={"X-Member-Id": str(SUPER),
                            "X-Role": "admin"})
    record("HTTP未知商品404", r.status_code == 404,
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
