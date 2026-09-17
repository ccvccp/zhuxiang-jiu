"""生产 E2E: 后台管理模块全链实证(17 端点)

五条链:
    A 管理员管理(compat引导): create-user×2 → list → detail →
      update → reset-password
    B 登录会话链: login(密码+会话Token) → dashboard(Token) →
      错误密码409(失败计数) → logout → 旧Token 401
    C 2FA链(TOTP): setup(secret+URI) → enable(totp_at实码)
      → 无码login(pendingToken) → pending调端点403 →
      2fa/verify(完成登录) → 带码login直通
    D 角色/权限/配置/日志: create-role → list → assign-
      permissions → create-config → list → logs → dashboard
    负测: 弱密码409 / 重复用户名409 / 无头403

清理: admin域差集(string) + username_index/code_index
    HDEL字段 + user_ids/config keys/role ids SREM成员 +
    user_roles列表键; seq四键留痕.
"""
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
A = {"X-Role": "admin"}
MARK = "LT后台E2E"
PWD = "LTAdmin#2026x"

c = httpx.Client(base_url=BASE, timeout=60)
r = redis.Redis(host="redis", port=6379, decode_responses=True)

before = set(r.keys("zhuxiang:admin:*"))
print(f"[inv] admin keys before: {len(before)} (生产从未初始化)")

# ============================================================
# A 管理员管理链(compat 模式 X-Role 引导)
# ============================================================

resp = c.post("/api/admin/users", headers=A, json={
    "username": f"{MARK}超管", "password": PWD,
    "realName": "LT超管", "employeeNo": "LT001",
    "department": "LT部", "position": "超管", "phone": "13800000000",
    "email": "lt1@zxjiu.com"})
u1 = resp.json().get("data") or {}
uid1 = u1.get("id")
print(f"[A1] create super: HTTP {resp.status_code} id={uid1} "
      f"username={u1.get('username')} status={u1.get('status')}")

resp = c.post("/api/admin/users", headers=A, json={
    "username": f"{MARK}用户2", "password": PWD,
    "realName": "LT用户2", "department": "LT部"})
u2 = resp.json().get("data") or {}
uid2 = u2.get("id")
print(f"[A2] create user2: HTTP {resp.status_code} id={uid2}")

resp = c.post("/api/admin/users", headers=A, json={
    "username": f"{MARK}超管", "password": PWD})
print(f"[A3] duplicate username: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error')}")

resp = c.post("/api/admin/users", headers=A, json={
    "username": f"{MARK}弱密", "password": "short"})
print(f"[A4] weak password: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error')}")

resp = c.get("/api/admin/users", headers=A)
ul = resp.json().get("data") or []
print(f"[A5] list users: HTTP {resp.status_code} count={len(ul)} "
      f"hit={any(u.get('id') == uid1 for u in ul)}")

resp = c.get(f"/api/admin/users/{uid2}", headers=A)
print(f"[A6] get user: HTTP {resp.status_code} "
      f"name={(resp.json().get('data') or {}).get('username')}")

resp = c.put(f"/api/admin/users/{uid2}", headers=A,
             json={"department": "LT部V2", "position": "LT岗"})
print(f"[A7] update user: HTTP {resp.status_code} "
      f"dept={(resp.json().get('data') or {}).get('department')}")

resp = c.post(f"/api/admin/users/{uid2}/reset-password", headers=A,
              json={"newPassword": "LTAdmin#2027y"})
print(f"[A8] reset password: HTTP {resp.status_code}")

# ============================================================
# B 登录会话链
# ============================================================

resp = c.post("/api/admin/login", json={
    "username": f"{MARK}超管", "password": PWD,
    "ip": "10.0.0.1", "device": "LT-E2E"})
lg = resp.json().get("data") or {}
token = lg.get("sessionToken")
print(f"[B1] login: HTTP {resp.status_code} userId={lg.get('userId')} "
      f"mustChangePwd={lg.get('mustChangePassword')} "
      f"perms={len(lg.get('permissions') or [])} "
      f"token={str(token)[:12]}...")

resp = c.get("/api/admin/dashboard", headers={"X-Admin-Token": token})
d = resp.json().get("data") or {}
print(f"[B2] dashboard(token): HTTP {resp.status_code} "
      f"members={d.get('totalMembers')} orders={d.get('totalOrders')} "
      f"admins={d.get('totalAdminUsers')} roles={d.get('totalRoles')}")

resp = c.post("/api/admin/login", json={
    "username": f"{MARK}超管", "password": "Wrong#Pwd999z"})
print(f"[B3] wrong password: HTTP {resp.status_code} "
      f"(expect 409) err={resp.json().get('error')}")

resp = c.post("/api/admin/logout",
              headers={"X-Admin-Token": token})
print(f"[B4] logout: HTTP {resp.status_code}")

resp = c.get("/api/admin/dashboard", headers={"X-Admin-Token": token})
print(f"[B5] dashboard after logout: HTTP {resp.status_code} "
      f"(expect 401) err={resp.json().get('error')}")

# ============================================================
# C 2FA 链(TOTP)
# ============================================================

resp = c.post("/api/admin/login", json={
    "username": f"{MARK}用户2", "password": "LTAdmin#2027y",
    "ip": "10.0.0.2", "device": "LT-E2E"})
lg2 = resp.json().get("data") or {}
token2 = lg2.get("sessionToken")
print(f"[C1] login user2: HTTP {resp.status_code} "
      f"token={str(token2)[:12]}...")

resp = c.post("/api/admin/2fa/setup",
              headers={"X-Admin-Token": token2})
setup = resp.json().get("data") or {}
secret = setup.get("totpSecret") or setup.get("secret")
print(f"[C2] 2fa setup: HTTP {resp.status_code} "
      f"secret={str(secret)[:8]}... "
      f"uri={str(setup.get('otpauthUri', setup.get('uri')))[:50]}")

from core.totp import totp_at
code = totp_at(secret)
resp = c.post("/api/admin/2fa/enable",
              headers={"X-Admin-Token": token2},
              json={"totpCode": code})
print(f"[C3] 2fa enable: HTTP {resp.status_code} "
      f"{json.dumps(resp.json().get('data') or {}, ensure_ascii=False)[:80]}")

resp = c.post("/api/admin/login", json={
    "username": f"{MARK}用户2", "password": "LTAdmin#2027y",
    "ip": "10.0.0.2", "device": "LT-E2E"})
pend = resp.json().get("data") or {}
ptoken = pend.get("sessionToken")
print(f"[C4] login no totp: HTTP {resp.status_code} "
      f"2faRequired={pend.get('twoFactorRequired')} "
      f"pending={str(ptoken)[:12]}...")

resp = c.get("/api/admin/users", headers={"X-Admin-Token": ptoken})
print(f"[C5] pending session call: HTTP {resp.status_code} "
      f"(expect 403) err={resp.json().get('error')}")

code2 = totp_at(secret)
resp = c.post("/api/admin/2fa/verify",
              headers={"X-Admin-Token": ptoken},
              json={"totpCode": code2})
ver = resp.json().get("data") or {}
print(f"[C6] 2fa verify: HTTP {resp.status_code} "
      f"verified={ver.get('verified', 'ok')} "
      f"keys={sorted(ver.keys())[:6]}")

resp = c.post("/api/admin/login", json={
    "username": f"{MARK}用户2", "password": "LTAdmin#2027y",
    "totpCode": totp_at(secret), "ip": "10.0.0.2"})
lg3 = resp.json().get("data") or {}
print(f"[C7] login with totp: HTTP {resp.status_code} "
      f"2faRequired={lg3.get('twoFactorRequired')} "
      f"token={str(lg3.get('sessionToken'))[:12]}...")
c.post("/api/admin/logout", headers={"X-Admin-Token": lg3.get("sessionToken")})

# ============================================================
# D 角色/权限/配置/日志链
# ============================================================

resp = c.post("/api/admin/roles", headers=A, json={
    "roleCode": f"{MARK}_role", "roleName": "LT角色",
    "description": "LT", "dataScope": "all",
    "permissions": ["lt:view", "lt:edit"]})
rl = resp.json().get("data") or {}
rid = rl.get("id")
print(f"[D1] create role: HTTP {resp.status_code} id={rid} "
      f"code={rl.get('roleCode')}")

resp = c.get("/api/admin/roles", headers=A)
rlist = resp.json().get("data") or []
print(f"[D2] list roles: HTTP {resp.status_code} count={len(rlist)} "
      f"hit={any(x.get('id') == rid for x in rlist)}")

resp = c.post(f"/api/admin/users/{uid2}/permissions", headers=A,
              json={"roleIds": [rid]})
d = resp.json().get("data") or {}
print(f"[D3] assign permissions: HTTP {resp.status_code} "
      f"roles={d.get('roleCodes')} perms={d.get('permissions')}")

resp = c.post("/api/admin/configs", headers=A, json={
    "configKey": f"{MARK}_key", "configValue": "lt_value",
    "configType": "string", "module": "lt_module",
    "description": "LT配置"})
print(f"[D4] create config: HTTP {resp.status_code} "
      f"key={(resp.json().get('data') or {}).get('configKey')}")

resp = c.get("/api/admin/configs", headers=A,
             params={"module": "lt_module"})
cfgs = resp.json().get("data") or []
print(f"[D5] list configs: HTTP {resp.status_code} count={len(cfgs)} "
      f"hit={any(x.get('configKey') == f'{MARK}_key' for x in cfgs)}")

resp = c.get("/api/admin/logs", headers=A,
             params={"module": "auth", "limit": 10})
logs = resp.json().get("data") or []
print(f"[D6] logs: HTTP {resp.status_code} count={len(logs)} "
      f"actions={[x.get('action') for x in logs[:6]]}")

resp = c.get("/api/admin/dashboard", headers=A)
print(f"[D7] dashboard: HTTP {resp.status_code} "
      f"keys={sorted((resp.json().get('data') or {}).keys())[:9]}")

resp = c.get("/api/admin/dashboard")
print(f"[N1] no auth: HTTP {resp.status_code} (expect 403)")

# ============================================================
# 清理(差集 + 索引字段/成员回退)
# ============================================================

removed = 0
for k in set(r.keys("zhuxiang:admin:*")) - before:
    if k.endswith(":seq"):
        continue
    if r.type(k) in ("string", "list"):  # logs:list 为日志ID索引列表
        r.delete(k)
        removed += 1

# 共享索引回退(hash 字段 / set 成员 / list 键)
for uname in (f"{MARK}超管", f"{MARK}用户2", f"{MARK}弱密"):
    if r.hdel("zhuxiang:admin:user:username_index", uname):
        removed += 1
for uid in (uid1, uid2):
    if uid and r.srem("zhuxiang:admin:user:ids", uid):
        removed += 1
if uid2:
    r.delete(f"zhuxiang:admin:user_roles:{uid2}")
    removed += 1
if r.hdel("zhuxiang:admin:role:code_index", f"{MARK}_role"):
    removed += 1
if rid and r.srem("zhuxiang:admin:role:ids", rid):
    removed += 1
if r.srem("zhuxiang:admin:config:keys", f"{MARK}_key"):
    removed += 1

residual = [k for k in set(r.keys("zhuxiang:admin:*")) - before
            if not k.endswith(":seq")]
left_idx = (r.hgetall("zhuxiang:admin:user:username_index")
            or r.hkeys("zhuxiang:admin:user:username_index"))
print(f"[clean] removed={removed} residual={residual} "
      f"username_index_left={left_idx} "
      f"user_ids_left={r.smembers('zhuxiang:admin:user:ids')} "
      f"config_keys_left={r.smembers('zhuxiang:admin:config:keys')}")
print("ADMIN E2E DONE")
