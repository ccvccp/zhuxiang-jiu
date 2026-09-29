"""39号·WebAuthn 真实轨测试(协议层 + 业务链 + HTTP 面)

运行方式:
    python test_entry_webauthn.py

覆盖(设计 §1.2 P2 预留落地):
    - 软认证器: ES256 密钥对 + 手构 CTAP2 authenticatorData/
      attestationObject(fmt none)/clientDataJSON——fido2 服务端
      真实验签(非 mock 派生), 测试确定性可复现
    - 注册链: off 开关铁律 409 → begin/complete 落库(mode=webauthn)
      → 重复凭证拒 → 无挂起挑战拒
    - 登录链: begin/complete 真实验签 → 令牌签发 → 篡改签名拒
      → origin 不符拒 → 挑战一次性(重放拒) → signCount 递增留痕
    - 互操作: bio/list 可见 / bio_revoke 可吊销 → 吊销后登录拒
    - strict 兼容: ENTRY_BIO_MODE=strict 下 webauthn 凭证放行
"""
import asyncio
import json
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"
os.environ.pop("ENTRY_BIO_MODE", None)

PASS = 0
FAIL = 0
RESULTS = []

RP_ID = "localhost"
ORIGIN = "http://localhost:8000"
member_id = 777


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


# ----------------------------------------------------------------
# 软认证器(确定性 CTAP2 材料——fido2 真实验签)
# ----------------------------------------------------------------
class SoftAuthenticator:
    """ES256 软认证器: 构造注册 attestation / 登录 assertion"""

    def __init__(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        self.priv = ec.generate_private_key(
            ec.SECP256R1())
        self.cred_id = os.urandom(32)

    def _cose(self):
        from fido2.cose import ES256
        return ES256.from_cryptography_key(
            self.priv.public_key())

    def _client_data(self, kind: str, challenge: str,
                     origin: str = ORIGIN) -> str:
        return json.dumps({
            "type": f"webauthn.{kind}",
            "challenge": challenge,
            "origin": origin, "crossOrigin": False,
        })

    def make_credential(self, options: dict,
                        origin: str = ORIGIN) -> dict:
        """注册材料: attestationObject(cbor: fmt none)+clientDataJSON
        (浏览器 PublicKeyCredential.toJSON() 平铺形状——fido2 2.x)"""
        from fido2 import cbor
        from hashlib import sha256
        from fido2.utils import websafe_encode
        from fido2.webauthn import AttestedCredentialData
        import struct
        pk = options["publicKey"]
        auth_data = (
            sha256(pk["rp"]["id"].encode()).digest()
            + b"\x41"                       # UP|AT
            + struct.pack(">I", 1)          # signCount
            + bytes(AttestedCredentialData.create(
                b"\x00" * 16, self.cred_id, self._cose()))
        )
        client_json = self._client_data(
            "create", pk["challenge"], origin)
        att_obj = cbor.encode({
            "fmt": "none", "attStmt": {},
            "authData": auth_data})
        return {
            "id": websafe_encode(self.cred_id),
            "rawId": websafe_encode(self.cred_id),
            "type": "public-key",
            "response": {
                # fido2 2.x: clientDataJSON=b64u(明文JSON)
                "clientDataJSON": websafe_encode(
                    client_json.encode()),
                "attestationObject": websafe_encode(att_obj),
            },
        }

    def get_assertion(self, options: dict,
                      origin: str = ORIGIN,
                      sign_count: int = 5,
                      tamper: bool = False) -> dict:
        """登录材料: authenticatorData+signature(ES256 真签名)
        (浏览器 PublicKeyCredential.toJSON() 平铺形状)"""
        from hashlib import sha256
        from fido2.utils import websafe_encode
        import struct
        pk = options["publicKey"]
        auth_data = (
            sha256(pk["rpId"].encode()).digest()
            + b"\x01"                       # UP
            + struct.pack(">I", sign_count)
        )
        client_json = self._client_data(
            "get", pk["challenge"], origin)
        client_hash = sha256(client_json.encode()).digest()
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        # ES256: 对 authData+clientDataHash 原文签名
        # (cryptography ECDSA(SHA256) 内部做哈希——勿预哈希)
        sig = self.priv.sign(
            auth_data + client_hash,
            ec.ECDSA(hashes.SHA256()))
        if tamper:
            sig = sig[:-1] + bytes([sig[-1] ^ 0xFF])
        return {
            "id": websafe_encode(self.cred_id),
            "rawId": websafe_encode(self.cred_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": websafe_encode(
                    client_json.encode()),
                "authenticatorData": websafe_encode(auth_data),
                "signature": websafe_encode(sig),
            },
        }


async def run() -> bool:
    from repositories.store import reset_store
    reset_store()

    from services.auth_service import AuthService
    reg = await AuthService().register(
        phone="13900000777", password="test123456")
    member_id = int(reg["memberId"])

    from services.entry_service import EntryService
    svc = EntryService()

    # ==============================
    # [01] off 开关铁律
    # ==============================
    os.environ["ENTRY_WEBAUTHN_MODE"] = "off"
    try:
        await svc.webauthn_register_begin(member_id)
        record("off 开关铁律: register/begin 拒绝", False)
    except ValueError as e:
        record("off 开关铁律: register/begin 拒绝",
               "ENTRY_WEBAUTHN_MODE" in str(e), str(e))

    os.environ["ENTRY_WEBAUTHN_MODE"] = "real"

    # ==============================
    # [02] 注册链(begin → complete)
    # ==============================
    opts = await svc.webauthn_register_begin(member_id)
    pk = opts.get("publicKey") or {}
    record("注册挑战结构(challenge/user/rp)",
           bool(pk.get("challenge")) and pk.get("rp", {}).get("id") == RP_ID,
           str(pk)[:100])

    auth = SoftAuthenticator()
    reg_resp = auth.make_credential(opts)
    record("注册材料构造(attestationObject b64u)",
           isinstance(reg_resp["response"]["attestationObject"], str)
           and len(reg_resp["response"]["attestationObject"]) > 20)

    try:
        await svc.webauthn_register_complete(
            member_id, reg_resp)
        record("complete 未抛(链路就绪)", True)
    except Exception as e:
        record("注册链路打通", False, str(e)[:160])
        return False
    # ↑ 首断点失败则终止(后续全部依赖凭证落库)

    from repositories.entry_repository import EntryRepository
    repo = EntryRepository()
    bios = await repo.list_bio(member_id=member_id)
    wa = [b for b in bios if b.get("mode") == "webauthn"]
    record("凭证落 bio 表(mode=webauthn)", len(wa) == 1,
           f"n={len(wa)}")
    record("凭证材料留存(webauthnAttested)",
           bool(wa and wa[0].get("webauthnAttested")),
           str(wa[0].get("webauthnAttested"))[:40] if wa else "-")
    record("凭证 ID 形态(WA 前缀)",
           bool(wa) and wa[0]["credentialId"].startswith("WA"),
           wa[0]["credentialId"][:20] if wa else "-")
    cred_id = wa[0]["credentialId"]

    # 重复绑定同凭证 → 拒
    opts2 = await svc.webauthn_register_begin(member_id)
    try:
        await svc.webauthn_register_complete(
            member_id, auth.make_credential(opts2))
        # 软认证器新建实例才有新 credId——同实例同 credId 复用:
        record("同凭证重复绑定拒绝", False, "重复绑定被接受")
    except ValueError as e:
        record("同凭证重复绑定拒绝", "已绑定" in str(e), str(e))

    # 无挂起挑战直接 complete → 拒
    try:
        await svc.webauthn_register_complete(
            member_id, {"id": "x", "response": {}})
        record("无挂起挑战 complete 拒绝", False, "被接受")
    except ValueError as e:
        record("无挂起挑战 complete 拒绝",
               "挂起" in str(e) or "无注册" in str(e), str(e)[:80])

    # ==============================
    # [03] 登录链(真实验签 → 令牌)
    # ==============================
    lopts = await svc.webauthn_login_begin(cred_id)
    record("登录挑战结构(challenge/rpId)",
           bool((lopts.get("publicKey") or {}).get("challenge")),
           str(lopts)[:80])

    assertion = auth.get_assertion(lopts)
    result = await svc.webauthn_login_complete(
        cred_id, assertion)
    record("真实验签登录成功(status=authenticated)",
           result.get("status") == "authenticated",
           str(result)[:80])
    record("令牌签发(tokens)",
           bool((result.get("tokens") or {}).get("accessToken")),
           str((result.get("tokens") or {}).keys()))
    record("归会员正确", result.get("memberId") == member_id,
           str(result.get("memberId")))

    # 篡改签名 → 拒
    lopts2 = await svc.webauthn_login_begin(cred_id)
    try:
        await svc.webauthn_login_complete(
            cred_id, auth.get_assertion(lopts2, tamper=True))
        record("篡改签名拒绝", False, "被接受")
    except ValueError as e:
        record("篡改签名拒绝", True, str(e)[:80])

    # origin 不符 → 拒(fail-hard 校验)
    lopts3 = await svc.webauthn_login_begin(cred_id)
    try:
        await svc.webauthn_login_complete(
            cred_id, auth.get_assertion(
                lopts3, origin="https://evil.example.com"))
        record("不受信 origin 拒绝", False, "被接受")
    except Exception as e:
        record("不受信 origin 拒绝",
               "origin" in str(e).lower() or "origin" in str(e),
               str(e)[:80])

    # 挑战一次性(同 response 重放) → 拒
    lopts4 = await svc.webauthn_login_begin(cred_id)
    good = auth.get_assertion(lopts4)
    await svc.webauthn_login_complete(cred_id, good)
    try:
        await svc.webauthn_login_complete(cred_id, good)
        record("挑战一次性(重放拒绝)", False, "重放被接受")
    except ValueError as e:
        record("挑战一次性(重放拒绝)",
               "挂起" in str(e) or "无登录" in str(e), str(e)[:80])

    # signCount 递增留痕
    bio_now = await repo.get_bio(cred_id)
    record("signCount 递增留痕",
           int(bio_now.get("signCount") or 0) >= 2,
           str(bio_now.get("signCount")))

    # ==============================
    # [04] strict 兼容(ENTRY_BIO_MODE=strict 下 webauthn 放行)
    # ==============================
    os.environ["ENTRY_BIO_MODE"] = "strict"
    try:
        lopts5 = await svc.webauthn_login_begin(cred_id)
        r5 = await svc.webauthn_login_complete(
            cred_id, auth.get_assertion(lopts5))
        record("strict 模式 webauthn 凭证放行",
               r5.get("status") == "authenticated", str(r5)[:60])
    except Exception as e:
        record("strict 模式 webauthn 凭证放行", False, str(e)[:100])
    os.environ.pop("ENTRY_BIO_MODE", None)

    # ==============================
    # [05] 互操作(bio 管理面)
    # ==============================
    listing = await svc.bio_list(member_id)
    record("bio/list 可见 webauthn 凭证",
           any(c.get("credentialId") == cred_id
               for c in listing),
           f"n={len(listing)}")
    revoked = await svc.bio_revoke(member_id, cred_id)
    record("bio_revoke 可吊销 webauthn 凭证",
           revoked.get("status") == "revoked",
           str(revoked.get("status")))
    try:
        await svc.webauthn_login_begin(cred_id)
        record("吊销后登录拒绝", False, "被接受")
    except ValueError as e:
        record("吊销后登录拒绝", "吊销" in str(e), str(e)[:60])

    return True


async def run_http():
    """HTTP 面: 4 端点鉴权/白名单语义"""
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    os.environ["ENTRY_WEBAUTHN_MODE"] = "off"

    print("[HTTP 面]")
    # _handle: ValueError → 409(开关 off 属状态冲突);
    # register 两端点须登录态——46+1 后裸头被剥(401 先行)
    r = client.post("/api/entry/webauthn/register/begin",
                    headers={"X-Member-Id": "777"})
    record("HTTP register/begin 裸头被剥 401(46+1 语义)",
           r.status_code == 401, f"s={r.status_code}")

    # 真实 Bearer 轨: 46+1 后 token 注入 X-Member-Id, off 铁律生效
    from services.auth_service import AuthService
    tok = (await AuthService().login(
        "13900000777", "test123456")).get("accessToken", "")
    r = client.post(
        "/api/entry/webauthn/register/begin",
        headers={"Authorization": f"Bearer {tok}"})
    record("HTTP register/begin Bearer off 拒(409)",
           r.status_code == 409, f"s={r.status_code}")

    r = client.post("/api/entry/webauthn/login/begin",
                    json={"credentialId": "WAxx"})
    record("HTTP login/begin off 拒(409)",
           r.status_code == 409, f"s={r.status_code}")

    r = client.post("/api/entry/webauthn/register/begin")
    record("HTTP register/begin 无登录态 401 先行",
           r.status_code == 401, f"s={r.status_code}")

    # 登录通道白名单生效: 无 Bearer 不 403(离关卡, 走业务错误)
    r = client.post("/api/entry/webauthn/login/complete",
                    json={"credentialId": "WAxx",
                          "response": {}})
    record("HTTP login/complete 白名单放行(非 403)",
           r.status_code != 403, f"s={r.status_code}")


def main() -> int:
    global PASS, FAIL
    ok = asyncio.run(run())
    if ok:
        asyncio.run(run_http())
    print("\n" + "=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"WebAuthn 真实轨测试: {PASS} 通过 / {FAIL} 失败")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
