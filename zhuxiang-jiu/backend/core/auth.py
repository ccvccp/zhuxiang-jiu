"""用户认证核心: JWT 签发/校验 + PBKDF2 密码哈希

设计原则:
    - 纯 Python 标准库实现(hmac/hashlib/base64/secrets), 不依赖 PyJWT/python-jose
    - HS256 对称加密, 密钥经 JWT_SECRET 环境变量注入(生产必改)
    - Access Token 短时效(2h) + Refresh Token 长时效(7d) 双令牌机制
    - 每个 Token 携带唯一 jti(UUID), 登出/刷新时加入黑名单实现吊销

密码策略(向后兼容):
    - 新密码: PBKDF2-HMAC-SHA256, 12 万轮迭代, 每用户独立随机盐
      存储格式: pbkdf2_sha256$120000${salt_hex}${hash_hex}
    - 旧密码(存量会员): 固定盐 SHA256(member_service._hash_password 旧格式)
      校验通过后自动升级为 PBKDF2 格式

Token 载荷结构:
    {"sub": 会员ID, "role": "member|admin", "type": "access|refresh",
     "jti": "uuid4", "iat": 签发时间戳, "exp": 过期时间戳}
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from typing import Optional

# ============================================================
# 配置(运行时读取, 不在模块级冻结)
# ============================================================

DEFAULT_JWT_SECRET = "zhuxiang-dev-secret-CHANGE-ME-in-production"
ACCESS_TOKEN_TTL = int(os.environ.get("JWT_ACCESS_TTL", 2 * 3600))       # 2 小时
REFRESH_TOKEN_TTL = int(os.environ.get("JWT_REFRESH_TTL", 7 * 24 * 3600))  # 7 天

PBKDF2_ITERATIONS = 120_000
_PBKDF2_PREFIX = "pbkdf2_sha256"
# 旧格式固定盐(与 services/member_service.py 的 _hash_password 保持一致)
_LEGACY_SALT = "zhuxiang_member_salt_v1"


class AuthError(Exception):
    """认证失败(401): Token 缺失/格式错误/过期/被吊销"""


class TokenExpiredError(AuthError):
    """Token 已过期(401, 提示客户端刷新)"""


# ============================================================
# 密码哈希
# ============================================================

def hash_password(password: str) -> str:
    """PBKDF2 哈希(每用户独立随机盐)"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), PBKDF2_ITERATIONS
    ).hex()
    return f"{_PBKDF2_PREFIX}${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, hashed: str) -> bool:
    """校验密码, 兼容 PBKDF2 新格式与固定盐 SHA256 旧格式"""
    if not hashed:
        return False
    if hashed.startswith(f"{_PBKDF2_PREFIX}$"):
        try:
            _, iterations, salt, digest = hashed.split("$", 3)
            candidate = hashlib.pbkdf2_hmac(
                "sha256", password.encode(), salt.encode(), int(iterations)
            ).hex()
            return hmac.compare_digest(candidate, digest)
        except (ValueError, TypeError):
            return False
    # 旧格式(固定盐 SHA256, 存量会员)
    legacy = hashlib.sha256(f"{_LEGACY_SALT}:{password}".encode()).hexdigest()
    return hmac.compare_digest(legacy, hashed)


def is_legacy_password_hash(hashed: str) -> bool:
    """是否为旧格式哈希(校验通过后应升级为 PBKDF2)"""
    return bool(hashed) and not hashed.startswith(f"{_PBKDF2_PREFIX}$")


# ============================================================
# JWT 编解码(HS256, RFC 7519 子集)
# ============================================================

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _get_secret() -> str:
    """JWT 密钥(运行时读取环境变量, 生产环境必须通过 JWT_SECRET 注入)"""
    return os.environ.get("JWT_SECRET", DEFAULT_JWT_SECRET)


def create_token(member_id, role: str = "member", token_type: str = "access",
                 ttl: Optional[int] = None, secret: Optional[str] = None) -> str:
    """签发 JWT

    Args:
        member_id: 会员ID(sub 载荷)
        role: 角色(member/admin)
        token_type: access 或 refresh
        ttl: 有效期秒数(默认按 token_type 取 ACCESS/REFRESH_TTL)
        secret: 覆盖默认密钥(测试用)
    """
    if ttl is None:
        ttl = ACCESS_TOKEN_TTL if token_type == "access" else REFRESH_TOKEN_TTL
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": member_id,
        "role": role,
        "type": token_type,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + ttl,
    }
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    key = (secret or _get_secret()).encode()
    signature = hmac.new(key, signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(signature)}"


def create_token_pair(member_id, role: str = "member",
                      secret: Optional[str] = None) -> dict:
    """签发 access + refresh 双令牌"""
    return {
        "accessToken": create_token(member_id, role, "access", secret=secret),
        "refreshToken": create_token(member_id, role, "refresh", secret=secret),
        "tokenType": "Bearer",
        "expiresIn": ACCESS_TOKEN_TTL,
    }


def decode_token(token: str, expected_type: Optional[str] = None,
                 secret: Optional[str] = None) -> dict:
    """校验并解码 JWT

    Returns:
        payload dict(sub/role/type/jti/iat/exp)

    Raises:
        AuthError: 格式错误/签名不匹配/type 不符
        TokenExpiredError: 已过期
    """
    if not token:
        raise AuthError("Token 不能为空")
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("Token 格式错误")
    header_b64, payload_b64, sig_b64 = parts

    # 签名校验(防篡改)
    try:
        signing_input = f"{header_b64}.{payload_b64}".encode()
        expected_sig = hmac.new(
            (secret or _get_secret()).encode(), signing_input, hashlib.sha256
        ).digest()
        actual_sig = _b64url_decode(sig_b64)
    except Exception as exc:  # base64 解码失败等
        raise AuthError("Token 格式错误") from exc
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise AuthError("Token 签名不匹配")

    # 载荷解析
    try:
        payload = json.loads(_b64url_decode(payload_b64))
        alg = json.loads(_b64url_decode(header_b64)).get("alg")
    except Exception as exc:
        raise AuthError("Token 载荷解析失败") from exc
    if alg != "HS256":
        raise AuthError(f"不支持的算法: {alg}")

    # 类型校验(refresh 不能当 access 用, 反之亦然)
    if expected_type and payload.get("type") != expected_type:
        raise AuthError(f"Token 类型错误(期望 {expected_type}, 实际 {payload.get('type')})")

    # 过期校验
    exp = payload.get("exp", 0)
    if time.time() >= exp:
        raise TokenExpiredError("Token 已过期")

    return payload


def remaining_ttl(payload: dict) -> int:
    """Token 剩余有效秒数(用于黑名单 TTL)"""
    return max(0, int(payload.get("exp", 0)) - int(time.time()))
