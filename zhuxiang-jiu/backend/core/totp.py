"""TOTP 双因素认证工具(RFC 6238, 纯标准库实现, 免外部依赖)

算法: HMAC-SHA1 + 30 秒时间窗 + 6 位数字(与 Google Authenticator 等主流
验证器 App 兼容), 校验允许 ±1 窗口时钟漂移。

用途(P0-5): 后台管理高危角色(超管/财务/审计)强制 2FA。
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

# TOTP 参数(与主流验证器 App 默认一致)
TIME_STEP = 30      # 时间窗(秒)
DIGITS = 6          # 验证码位数
DRIFT_WINDOW = 1    # 校验允许的时钟漂移窗口数(±30s)
ISSUER = "zhuxiang-jiu"  # otpauth URI 中的发行方


def generate_secret() -> str:
    """生成 Base32 随机密钥(160 bit)"""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _decode_secret(secret: str) -> bytes:
    """Base32 解码(自动补 padding)"""
    padded = secret.upper() + "=" * ((8 - len(secret) % 8) % 8)
    return base64.b32decode(padded)


def totp_at(secret: str, timestamp: float = None) -> str:
    """计算指定时刻的 6 位 TOTP 码"""
    counter = int((timestamp if timestamp is not None else time.time())
                  // TIME_STEP)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(_decode_secret(secret), msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0]
            & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(code).zfill(DIGITS)


def verify_totp(secret: str, code: str) -> bool:
    """校验 TOTP 验证码(恒定时间比较, 允许 ±1 时间窗漂移)"""
    if not secret or not code or not str(code).isdigit() \
            or len(str(code)) != DIGITS:
        return False
    now = int(time.time())
    for drift in range(-DRIFT_WINDOW, DRIFT_WINDOW + 1):
        expected = totp_at(secret, now + drift * TIME_STEP)
        if hmac.compare_digest(expected, str(code)):
            return True
    return False


def provisioning_uri(secret: str, account: str,
                     issuer: str = ISSUER) -> str:
    """生成 otpauth:// URI(验证器 App 扫码绑定用)"""
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}"
            f"?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={TIME_STEP}")
