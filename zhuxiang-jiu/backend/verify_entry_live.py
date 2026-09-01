# -*- coding: utf-8 -*-
"""39号实机部署验收脚本(Redis 模式容器)

覆盖: 入口识别/统一登录风控/step_up/扫码全协议/可信设备/
生物凭证两段协议/决策回流/落地页/看板/白名单。
用法: python verify_entry_live.py
"""
import hashlib
import json
import sys
import urllib.request

B = "http://127.0.0.2:8000"  # 直达Docker容器
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


def record(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} -- {detail}")


ADMIN = {"X-Member-Id": "2", "X-Role": "admin"}
FP_A = "ua=Chrome|screen=1920x1080|lang=zh-CN|tz=Asia/Shanghai"
FP_B = "ua=Firefox|screen=1440x900|lang=zh-CN|tz=Asia/Shanghai"

print("=" * 62)
print("39号实机部署验收(容器 Redis 模式)")
print("=" * 62)

# ---------- 1. 入口识别与白名单 ----------
print("\n[1. 入口识别与白名单]")
s, r = req("GET", "/api/entry/recognize?fingerprint="
           + urllib.parse.quote(FP_A))
d = r.get("data") or {}
record("recognize白名单公开(首访)",
       s == 200 and d.get("knownDevice") is False
       and len(d.get("recommendedModes") or []) == 4,
       f"status={s}")

s, r = req("GET", "/api/entry/devices")
record("设备清单未登录401", s == 401, f"status={s}")

# ---------- 2. 统一登录(AI风控) ----------
print("\n[2. 统一登录与风控]")
# 容器 auth 种子: 13800000002/test123456(需注册口径核对——
# auth 注册走 PBKDF2, 先注册实机账号)
import urllib.parse
s, r = req("POST", "/api/auth/register",
           {"phone": "13911110001", "password": "Live1234!",
            "nickname": "实机入口会员", "ageConfirmed": True})
mid = (r.get("memberId") or 0) if s == 200 else 0
if not mid:
    # 已注册 → 登录取 memberId
    s2, r2 = req("POST", "/api/auth/login",
                 {"phone": "13911110001", "password": "Live1234!"})
    mid = (r2.get("memberId") or 0) if s2 == 200 else 0
record("实机会员就绪(auth注册/登录)", mid > 0,
       f"status={s} mid={mid}")

s, r = req("POST", "/api/entry/login",
           {"mode": "password", "phone": "13911110001",
            "password": "Live1234!", "fingerprint": FP_A},
           {"X-Forwarded-For": "10.0.0.1"})
d = r.get("data") or {}
record("密码登录风控决策(allow/step_up)",
       s == 200 and d.get("status") in ("authenticated",
                                        "step_up_required")
       and (d.get("decision") or {}).get("action"),
       f"status={s} {d.get('status')}/"
       f"{(d.get('decision') or {}).get('action')}")

s, r = req("POST", "/api/entry/login",
           {"mode": "password", "phone": "13911110001",
            "password": "Wrong!", "fingerprint": FP_A},
           {"X-Forwarded-For": "10.0.0.1"})
record("错误密码409", s == 409, f"status={s}")

s, r = req("POST", "/api/entry/login",
           {"mode": "face", "phone": "13911110001"})
record("非法通道409", s == 409, f"status={s}")

# ---------- 3. 可信设备 ----------
print("\n[3. 可信设备]")
MEM = {"X-Member-Id": str(mid)}
s, r = req("GET", "/api/entry/devices", None, MEM)
devices = r.get("data") or []
record("登录即记设备", s == 200 and len(devices) >= 1,
       f"status={s} {len(devices)}台")

dv = (devices[0].get("deviceId") or "") if devices else ""
if dv:
    s, r = req("POST", f"/api/entry/devices/{dv}/trust",
               {"days": 30}, MEM)
    record("开启30天信任", s == 200
           and (r.get("data") or {}).get("trusted"), f"status={s}")

    s, r = req("DELETE", f"/api/entry/devices/{dv}", None, MEM)
    record("删除设备", s == 200, f"status={s}")

# ---------- 4. 扫码登录全协议 ----------
print("\n[4. 扫码登录协议]")
s, qr = req("POST", "/api/entry/qr/create", {"fingerprint": FP_A})
qr_id = (qr.get("data") or {}).get("qrId", "")
record("创建扫码会话", s == 200 and qr_id
       and (qr.get("data") or {}).get("qrPayload")
       == f"ZXBJ-ENTRY:{qr_id}", f"status={s}")

s, r = req("GET", f"/api/entry/qr/{qr_id}/status")
record("轮询pending(GET白名单)", s == 200
       and (r.get("data") or {}).get("status") == "pending",
       f"status={s}")

s, r = req("POST", f"/api/entry/qr/{qr_id}/confirm", None, MEM)
record("未扫描直接确认409", s == 409, f"status={s}")

s, r = req("POST", f"/api/entry/qr/{qr_id}/scan", {})
record("扫码动作scanned", s == 200
       and (r.get("data") or {}).get("status") == "scanned",
       f"status={s}")

s, conf = req("POST", f"/api/entry/qr/{qr_id}/confirm", None,
              {**MEM, "X-Forwarded-For": "127.0.0.1"})
ticket = (conf.get("data") or {}).get("loginTicket", "")
record("手机端确认生成ticket", s == 200 and ticket.startswith("LT"),
       f"status={s}")

s, ex = req("POST", f"/api/entry/qr/{qr_id}/exchange",
            {"loginTicket": ticket})
tokens = ((ex.get("data") or {}).get("tokens") or {})
record("ticket换JWT令牌", s == 200
       and tokens.get("accessToken"), f"status={s}")

s, ex2 = req("POST", f"/api/entry/qr/{qr_id}/exchange",
             {"loginTicket": ticket})
record("票据防重放409", s == 409, f"status={s}")

# ---------- 5. 生物凭证(Mock轨) ----------
print("\n[5. 生物凭证]")
s, en = req("POST", "/api/entry/bio/enroll",
            {"bioType": "fingerprint", "deviceId": "DV_LIVE_01"},
            MEM)
en_d = en.get("data") or {}
record("enroll设备挑战", s == 200
       and en_d.get("enrollChallenge", "").startswith("BC"),
       f"status={s}")

pkh = hashlib.sha256(b"live-public-key").hexdigest()[:32]
s, cred = req("POST", "/api/entry/bio/bind",
              {"bioType": "fingerprint", "deviceId": "DV_LIVE_01",
               "enrollChallenge": en_d.get("enrollChallenge", ""),
               "publicKeyHash": pkh, "name": "实机指纹"}, MEM)
cred_id = (cred.get("data") or {}).get("credentialId", "")
record("bind摘要凭证(合规红线)", s == 200 and cred_id
       and (cred.get("data") or {}).get("publicKeyHash") == pkh,
       f"status={s}")

s, ch = req("POST", "/api/entry/bio/challenge",
            {"credentialId": cred_id})
ch_d = ch.get("data") or {}
assertion = hashlib.sha256(
    (ch_d.get("assertionChallenge", "") + "DV_LIVE_01")
    .encode()).hexdigest()[:32]
record("challenge公开(白名单)", s == 200
       and ch_d.get("assertionChallenge", "").startswith("AC"),
       f"status={s}")

s, v = req("POST", "/api/entry/bio/verify",
           {"credentialId": cred_id, "assertionHash": assertion},
           {"X-Forwarded-For": "127.0.0.1"})
v_d = v.get("data") or {}
record("verify断言+风控+签发", s == 200
       and v_d.get("status") in ("authenticated",
                                 "step_up_required"),
       f"status={s} {v_d.get('status')}")

s, r = req("DELETE", f"/api/entry/bio/credentials/{cred_id}",
           None, MEM)
record("吊销凭证", s == 200
       and (r.get("data") or {}).get("status") == "revoked",
       f"status={s}")

# ---------- 6. 决策回流与看板 ----------
print("\n[6. 决策回流与看板]")
s, r = req("GET", "/api/entry/decisions?limit=20", None,
           {"X-Role": "admin"})
decisions = r.get("data") or []
record("决策留痕列表(admin)", s == 200 and len(decisions) >= 2,
       f"status={s} {len(decisions)}条")

if decisions:
    did = decisions[0].get("decisionId")
    s, r = req("POST", f"/api/entry/decisions/{did}/review",
               {"verdict": "confirm"}, {"X-Role": "admin"})
    record("决策复核回流ai_learning", s == 200
           and (r.get("data") or {}).get("correct") is True,
           f"status={s}")
    s, r = req("POST", f"/api/entry/decisions/{did}/review",
               {"verdict": "confirm"}, {"X-Role": "admin"})
    record("重复复核409幂等", s == 409, f"status={s}")

s, r = req("GET", "/api/entry/report/overview", None,
           {"X-Role": "admin"})
ov = r.get("data") or {}
record("看板overview(通道+动作分布)",
       s == 200 and "modeStats" in ov and "actionStats" in ov,
       f"status={s}")

s, r = req("GET", "/api/entry/events?limit=20", None,
           {"X-Role": "admin"})
record("事件流水端点", s == 200 and (r.get("count") or 0) >= 1,
       f"status={s}")

s, r = req("GET", "/api/entry/events")
record("事件流水越权403", s == 403, f"status={s}")

# ---------- 7. 落地页 ----------
print("\n[7. 角色落地页]")
s, r = req("GET", f"/api/entry/landing?role=member&memberId={mid}")
ld = r.get("data") or {}
record("member落地页(chips+连登)",
       s == 200 and len(ld.get("chips") or []) >= 4
       and "loginStreak" in ld, f"status={s}")

s, r = req("GET", "/api/entry/landing?role=admin")
record("admin落地页", s == 200
       and len((r.get("data") or {}).get("chips") or []) >= 1,
       f"status={s}")

print("\n" + "-" * 62)
print(f"实机验收总计: {PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
