"""39号·WebAuthn 真实轨协议层(P2 预留落地)

设计文档 §1.2 预留"真实 WebAuthn/ctap(P2)"——mock 轨(bio 派生
断言)零改动叠加, 本层封装 python-fido2 服务端: 注册挑战/断言验签
全真实 CTAP2 协议, 平台认证器本地验证生物特征, 原始生物数据
永不上送红线天然满足(浏览器 navigator.credentials 本地完成)。

铁律:
    - 凭证落 39号 bio 表(mode=webauthn), AttestedCredentialData
      websafe 编码入字段(entry_repository 泛 dict 直存无白名单)
    - 挑战挂起态(state)进程内存 + TTL 300s 惰性清理;
      多实例部署演进: state 迁 Redis(bio_challenge 同款范式)
    - RP id/origins 可配(ENTRY_WEBAUTHN_RP_ID/ORIGINS),
      origin 校验 fail-hard(生产 zxjiu.com 显式配置)
    - sign_count 单调递增校验(克隆检测, fido2 内建)开箱即得
"""

import os
import time
from hashlib import sha256

from fido2.server import Fido2Server
from fido2.utils import websafe_decode, websafe_encode
from fido2.webauthn import (
    AttestedCredentialData,
    PublicKeyCredentialUserEntity,
)

# 挑战挂起态 TTL(秒)与进程内存态
_STATE_TTL = 300
_pending: dict = {}


def _rp_id() -> str:
    return (os.environ.get("ENTRY_WEBAUTHN_RP_ID")
            or "localhost").strip()


def _rp_name() -> str:
    return (os.environ.get("ENTRY_WEBAUTHN_RP_NAME")
            or "zhuxiang-jiu").strip()


def _origins() -> list[str]:
    raw = (os.environ.get("ENTRY_WEBAUTHN_ORIGINS")
           or "http://localhost:8000,http://localhost:8080,"
              "http://127.0.0.1:8000")
    return [o.strip() for o in raw.split(",") if o.strip()]


def _verify_origin(origin: str) -> bool:
    """origin fail-hard 校验(fido2 回调)"""
    if origin in _origins():
        return True
    raise ValueError(
        f"WebAuthn origin 不受信任({origin}, "
        f"允许: {_origins()})")


def _server() -> Fido2Server:
    """Fido2Server 单例(懒建——RP 配置进程内固定)"""
    global _fido2_server
    try:
        return _fido2_server
    except NameError:
        pass
    _fido2_server = Fido2Server(
        {"id": _rp_id(), "name": _rp_name()},
        attestation="none",
        verify_origin=_verify_origin)
    return _fido2_server


def _gc_pending() -> None:
    """惰性清理过期挑战挂起态"""
    now = time.time()
    for k in [k for k, v in _pending.items()
              if now - v["at"] > _STATE_TTL]:
        _pending.pop(k, None)


class EntryWebauthnService:
    """WebAuthn 真实轨协议层(纯协议, 业务在 entry_service)"""

    async def register_begin(self,
                             member_id: int) -> dict:
        """注册挑战: 前端 navigator.credentials.create(
        publicKey=options.publicKey) 直接可用"""
        _gc_pending()
        user = PublicKeyCredentialUserEntity(
            id=f"member-{member_id}".encode(),
            name=f"member-{member_id}",
            display_name=f"竹香会员 {member_id}")
        options, state = _server().register_begin(user)
        _pending[f"reg:{member_id}"] = {
            "state": state, "at": time.time()}
        return {"publicKey": dict(options)["publicKey"],
                "stateTtl": _STATE_TTL,
                "rpId": _rp_id(),
                "hint": "浏览器本地创建凭证, "
                        "原始生物数据不上送"}

    async def register_complete(self, member_id: int,
                                response: dict) -> dict:
        """注册完成: fido2 验证 attestation → 返回可落库材料

        Raises:
            ValueError: 无挂起挑战/attestation 校验失败
        """
        _gc_pending()
        entry = _pending.pop(f"reg:{member_id}", None)
        if entry is None:
            raise ValueError(
                "无注册挑战挂起(请先 register/begin, 300s 内完成)")
        auth_data = _server().register_complete(
            entry["state"], response)
        # fido2 2.x 属性: credential_data(AttestedCredentialData)
        # / counter(signCount)——1.x 名称已迁移
        acd = auth_data.credential_data
        cred_id = websafe_encode(bytes(acd.credential_id))
        return {
            "webauthnAttested": websafe_encode(bytes(acd)),
            "credentialId": f"WA{cred_id[:24]}",
            "credentialIdHash": sha256(
                bytes(acd.credential_id)).hexdigest()[:32],
            "signCount": int(auth_data.counter or 0),
        }

    async def login_begin(self, bio_record: dict) -> dict:
        """登录挑战: 凭证 bio 记录 → assertion options

        Raises:
            ValueError: 凭证非 webauthn 轨(降级指引 mock 轨)
        """
        _gc_pending()
        attested = bio_record.get("webauthnAttested")
        if not attested:
            raise ValueError(
                "该凭证非 WebAuthn 轨(mode="
                f"{bio_record.get('mode')}, 走原 bio 轨)")
        credentials = [AttestedCredentialData(
            websafe_decode(attested))]
        options, state = _server().authenticate_begin(
            credentials)
        cred_id = bio_record["credentialId"]
        _pending[f"login:{cred_id}"] = {
            "state": state, "at": time.time()}
        return {"publicKey": dict(options)["publicKey"],
                "credentialId": cred_id,
                "stateTtl": _STATE_TTL}

    async def login_complete(self, bio_record: dict,
                             response: dict) -> dict:
        """登录完成: fido2 验签(sign_count 克隆检测内建)

        Raises:
            ValueError: 无挂起挑战/签名验证失败/克隆检测
        """
        _gc_pending()
        cred_id = bio_record["credentialId"]
        entry = _pending.pop(f"login:{cred_id}", None)
        if entry is None:
            raise ValueError(
                "无登录挑战挂起(请先 login/begin, 300s 内完成)")
        attested = AttestedCredentialData(
            websafe_decode(bio_record["webauthnAttested"]))
        _server().authenticate_complete(
            entry["state"], [attested], response)
        # fido2 验签内建: origin/rpIdHash/signCount 克隆检测
        return {"verified": True}
