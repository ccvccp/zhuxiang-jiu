# -*- coding: utf-8 -*-
"""38号实机部署验收脚本(Redis 模式容器)

覆盖: 权限矩阵 / 六态状态机全流转 / AI预审三档 / SoD / 版本回滚 /
图片中心(上传+审图) / AI设计工坊 / 智能下架建议 / 学习回流 / 看板。
用法: python verify_pdm_live.py
"""
import base64
import json
import subprocess
import sys
import urllib.request

B = "http://127.0.0.2:8000"  # 直达Docker容器(127.0.0.1被宿主机dev后端占用)
PASS = FAIL = 0


def req(method, path, body=None, headers=None):
    data = (json.dumps(body, ensure_ascii=False).encode("utf-8")
            if body is not None else None)
    r = urllib.request.Request(B + path, data=data, method=method)
    r.add_header("Content-Type", "application/json; charset=utf-8")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def login(phone, password):
    """登录取 JWT(perm 路由走 Depends 强校验, 须 Authorization 头)"""
    s, r = req("POST", "/api/auth/login",
               {"phone": phone, "password": password})
    token = (r.get("accessToken") or r.get("data", {}).get("accessToken")
             or "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def record(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} -- {detail}")


ADMIN = {"X-Member-Id": "2", "X-Role": "admin"}
# 容器种子会员: 2=admin(13800000002) / 1=member(13800000001)
# 注意: perm 路由走 JWT Depends 强校验, 而 member 种子密码是 member
# 模块 sha256 口径(auth 模块 PBKDF2 登录不通) → perm 操作走容器内
# 直调(37号惯例)


def docker_py(code: str):
    """容器内执行 Python(perm 直授/种子核对)"""
    r = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python", "-c", code],
        capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()

print("=" * 62)
print("38号实机部署验收(容器 Redis 模式)")
print("=" * 62)

# ---------- 1. 权限矩阵 ----------
print("\n[1. 权限矩阵]")
s, r = req("POST", "/api/pdm/products",
           {"name": "越权品", "price": 100})
record("无登录401", s == 401, f"status={s}")
# 容器种子会员1为member角色(无产品权限)
s, r = req("POST", "/api/pdm/products",
           {"name": "路人品", "price": 100},
           {"X-Member-Id": "1", "X-Role": "member"})
record("member无授权403", s == 403, f"status={s} {str(r)[:80]}")
# admin 兜底全权限
s, r = req("GET", "/api/pdm/report/overview", None, ADMIN)
ov = r.get("data") or {}
record("admin JWT角色兜底overview",
       s == 200 and "statusCounts" in ov, f"status={s}")

# perm 权限点种子(32=28生产流程+4产品域; 容器内直调核对)
rc, out, err = docker_py(
    "import asyncio\n"
    "from services.perm_service import PermService\n"
    "nodes = asyncio.run(PermService().list_nodes())\n"
    "print([n['code'] for n in nodes if n['code'].startswith('product.')])")
try:
    prod_codes = json.loads(out.replace("'", '"'))
except Exception:
    prod_codes = []
record("perm产品域4权限点种子",
       set(prod_codes) == {"product.view", "product.operate",
                           "product.approve", "product.manage"},
       f"rc={rc} out={out[:100]} err={err[:100]}")

# ---------- 2. 创建与AI预审 ----------
print("\n[2. 创建与AI预审]")
s, p1 = req("POST", "/api/pdm/products",
            {"name": "实机竹香·42°经典", "price": 888,
             "alcohol": 42, "stock": 10,
             "description": "实机验收测试商品。"}, ADMIN)
d1 = p1.get("data") or {}
pid = d1.get("product_id")
record("admin创建草稿draft", s == 200
       and d1.get("pdmStatus") == "draft", f"status={s} {str(d1)[:120]}")

# 消费端不可见(响应为顶层 products 列表, 须带分页参数)
s, pub = req("GET", "/api/product/list?page=1&pageSize=100")
items = pub.get("products") or []
record("草稿消费端不可见",
       not any(x.get("product_id") == pid for x in items))

s, p1 = req("POST", f"/api/pdm/products/{pid}/submit", None, ADMIN)
ai = p1.get("data", {}).get("aiReview") or {}
record("AI预审快车道(≥80)",
       p1.get("data", {}).get("pdmStatus") == "manual_reviewing"
       and ai.get("action") == "fast_track"
       and ai.get("score", 0) >= 80,
       f"状态={p1.get('data', {}).get('pdmStatus')} ai={ai.get('score')}")

# SoD: admin 建品并自审 → 409
s, r = req("POST", f"/api/pdm/products/{pid}/review",
           {"approved": True}, ADMIN)
record("SoD自审拦截409", s == 409 and "SoD" in str(r),
       f"status={s} {str(r)[:100]}")

# 违禁词品 → AI 拒
s, p3 = req("POST", "/api/pdm/products",
            {"name": "实机违规品", "price": 3000, "alcohol": 53,
             "description": "开怀畅饮不醉不归。"}, ADMIN)
pid3 = p3.get("data", {}).get("product_id")
s, p3 = req("POST", f"/api/pdm/products/{pid3}/submit", None, ADMIN)
record("违禁词AI拒rejected",
       p3.get("data", {}).get("pdmStatus") == "rejected"
       and (p3.get("data", {}).get("aiReview") or {}).get("action")
       == "reject",
       f"状态={p3.get('data', {}).get('pdmStatus')}")

# ---------- 3. 终审与上下架 ----------
print("\n[3. 终审与上下架]")
# 造审核员: 容器内直建会员 + admin 会员 + perm 直授 product.approve
# (Redis 无 member 种子, auth 注册走 member 模块双轨; 直建最稳)
rc, out, err = docker_py(
    "import asyncio\n"
    "from repositories.member_repository import MemberRepository\n"
    "from services.perm_service import PermService\n"
    "async def main():\n"
    "    mr = MemberRepository()\n"
    "    async def ensure(phone, role, nick):\n"
    "        m = await mr.get_by_phone(phone)\n"
    "        if m: return m\n"
    "        return await mr.create({'phone':phone,'password':'x',"
    "'nickname':nick,'avatar':'','gender':1,'level':1,"
    "'growth_value':0,'points':0,'status':1,'reg_source':'phone',"
    "'role':role})\n"
    "    admin = await ensure('13800009999','admin','实机管理员')\n"
    "    aud = await ensure('13800009998','member','实机审核员')\n"
    "    try:\n"
    "        g = await PermService().assign_grant(admin['id'], aud['id'],"
    " 'product.approve')\n"
    "        print('OK', aud['id'], g.get('grantId'))\n"
    "    except Exception as e:\n"
    "        if '已持有' in str(e):\n"
    "            print('OK', aud['id'], 'existing')\n"
    "        else:\n"
    "            print('GRANTFAIL', str(e)[:60])\n"
    "asyncio.run(main())")
auditor_id = 0
if out.startswith("OK"):
    parts = out.split()
    auditor_id = int(parts[1])
record("造审核员+perm授予product.approve",
       rc == 0 and out.startswith("OK"),
       f"rc={rc} out={out[:90]} err={err[:120]}")
auditor = {"X-Member-Id": str(auditor_id), "X-Role": "member"}

s, p1 = req("POST", f"/api/pdm/products/{pid}/review",
            {"approved": True, "note": "实机验收通过"}, auditor)
record("人工终审通过on_sale",
       s == 200 and p1.get("data", {}).get("pdmStatus") == "on_sale",
       f"status={s}")
# 消费端可见
s, pub = req("GET", "/api/product/list?page=1&pageSize=100")
items = pub.get("products") or []
record("在售消费端可见",
       any(x.get("product_id") == pid for x in items),
       f"count={len(items)}")

# 下架(原因必填) + 幂等 + 重新上架
s, r = req("POST", f"/api/pdm/products/{pid}/delist",
           {"reason": "   "}, ADMIN)
record("下架原因必填409", s == 409, f"status={s}")
s, r = req("POST", f"/api/pdm/products/{pid}/delist",
           {"reason": "实机例行下架"}, ADMIN)
record("下架off_sale", (r.get("data") or {}).get("pdmStatus")
       == "off_sale", f"status={s}")
s, r = req("POST", f"/api/pdm/products/{pid}/delist",
           {"reason": "重复下架"}, ADMIN)
record("下架幂等", (r.get("data") or {}).get("pdmStatus") == "off_sale")
s, r = req("POST", f"/api/pdm/products/{pid}/list", None, ADMIN)
record("重新上架on_sale", (r.get("data") or {}).get("pdmStatus")
       == "on_sale", f"status={s}")

# 紧急下架(任意态直达): rejected 的 pid3
s, r = req("POST", f"/api/pdm/products/{pid3}/force-delist",
           {"reason": "实机负面舆情演练"}, ADMIN)
record("紧急下架rejected→off_sale",
       (r.get("data") or {}).get("pdmStatus") == "off_sale",
       f"status={s}")

# ---------- 4. 编辑双轨与版本回滚 ----------
print("\n[4. 编辑双轨与版本回滚]")
s, r = req("PUT", f"/api/pdm/products/{pid}",
           {"description": "cosmetic微调描述。"}, ADMIN)
record("cosmetic不动在售态",
       (r.get("data") or {}).get("pdmStatus") == "on_sale",
       f"status={s}")
s, r = req("PUT", f"/api/pdm/products/{pid}",
           {"price": 999}, ADMIN)
record("substantive改价回落draft",
       (r.get("data") or {}).get("pdmStatus") == "draft"
       and (r.get("data") or {}).get("price") == 999,
       f"status={s}")

s, r = req("GET", f"/api/pdm/products/{pid}/versions", None, ADMIN)
versions = r.get("data") or []
record("版本列表(≥3版含双类型)",
       len(versions) >= 3
       and {v.get("changeType") for v in versions}
       >= {"cosmetic", "substantive"},
       f"实际{len(versions)}版")
s, r = req("POST", f"/api/pdm/products/{pid}/versions/rollback",
           {"version": 2}, ADMIN)
record("版本回滚须重审",
       (r.get("data") or {}).get("pdmStatus") in ("draft", "rejected"),
       f"status={s}")

# ---------- 5. 图片中心 ----------
print("\n[5. 图片中心]")
png = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080200000090"
    "7753de0000000c4944415408d763f8cfc000000301010018dd8db00000"
    "0000")).decode()
s, img = req("POST", "/api/pdm/images",
             {"dataBase64": png, "ext": ".png",
              "productName": "实机竹香"}, ADMIN)
img_d = img.get("data") or {}
record("上传图片+自动审图",
       s == 200 and img_d.get("status") == "usable"
       and (img_d.get("aiReview") or {}).get("mode") in ("rule", "vision"),
       f"status={s} {str(img_d)[:120]}")

# 在售商品换图 → 回落 draft
s, pf = req("POST", "/api/pdm/products",
            {"name": "实机换图品", "price": 888, "alcohol": 42,
             "description": "换图测试。"}, ADMIN)
pidf = pf.get("data", {}).get("product_id")
req("POST", f"/api/pdm/products/{pidf}/submit", None, ADMIN)
req("POST", f"/api/pdm/products/{pidf}/review",
    {"approved": True}, auditor)
s, r = req("PUT", f"/api/pdm/products/{pidf}/images",
           {"main": img_d.get("url", ""), "gallery": []}, ADMIN)
record("在售换图回落draft",
       (r.get("data") or {}).get("pdmStatus") == "draft",
       f"status={s}")

# flagged 重传/销毁
s, r = req("POST", f"/api/pdm/images/{img_d.get('imageId', 0)}/destroy",
           None, ADMIN)
record("非flagged销毁409", s == 409, f"status={s}")

# ---------- 6. AI 设计工坊 ----------
print("\n[6. AI设计工坊]")
s, r = req("POST",
           f"/api/pdm/products/{pidf}/design/generate-main-image",
           None, ADMIN)
gen = r.get("data") or {}
record("AI主图生成(轨+入库+审图)",
       s == 200 and (gen.get("design") or {}).get("track")
       and (gen.get("image") or {}).get("generated"),
       f"status={s} track={(gen.get('design') or {}).get('track')}")

s, r = req("POST", f"/api/pdm/products/{pidf}/design/copy-optimize",
           None, ADMIN)
copy_d = r.get("data") or {}
record("AI文案优化(仅建议)",
       s == 200 and copy_d.get("applied") is False
       and copy_d.get("title"), f"status={s}")

s, r = req("GET", f"/api/pdm/products/{pidf}/design/main-image-ab",
           None, ADMIN)
record("主图A/B建议", s == 200
       and "recommendation" in (r.get("data") or {}), f"status={s}")

# ---------- 7. 智能下架建议 ----------
print("\n[7. 智能下架建议]")
s, r = req("GET", "/api/pdm/listing-advice", None, ADMIN)
record("下架建议端点", s == 200 and isinstance(r.get("data"), list),
       f"status={s}")

# ---------- 8. 学习回流 ----------
print("\n[8. 学习回流]")
s, pb = req("POST", "/api/pdm/products",
            {"name": "实机回流品", "price": 888, "alcohol": 42,
             "description": "学习回流测试。"}, ADMIN)
pidb = pb.get("data", {}).get("product_id")
req("POST", f"/api/pdm/products/{pidb}/submit", None, ADMIN)
s, r = req("POST", f"/api/pdm/products/{pidb}/review",
           {"approved": True}, auditor)
record("终审自动回流", s == 200, f"status={s}")
s, r = req("POST", f"/api/pdm/products/{pidb}/learning-feedback",
           {"decision": "approve"}, ADMIN)
record("重复回流409幂等", s == 409, f"status={s}")

# ---------- 9. 看板 ----------
print("\n[9. 看板]")
s, r = req("GET", "/api/pdm/report/overview", None, ADMIN)
ov = r.get("data") or {}
record("overview六态+AI统计+审图模式",
       set((ov.get("statusCounts") or {})) >= {
           "draft", "manual_reviewing", "rejected",
           "on_sale", "off_sale"}
       and "aiStats" in ov
       and "reviewModes" in (ov.get("images") or {}),
       f"实际{str(ov)[:150]}")

print("\n" + "-" * 62)
print(f"实机验收总计: {PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
