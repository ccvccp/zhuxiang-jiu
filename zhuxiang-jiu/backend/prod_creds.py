"""生产/E2E 登录凭据单一入口(env 驱动 + fail-closed)

背景(2026-11-08):
    A 级泄露面 = 生产 admin 账号(手机号 + 同一口令)散落在约 20 个运维/
    验收脚本与 e2e_auth_helper.py 的模块级常量里, 直打 https://zyjiu.com。
    本模块把"从哪读、怎么校验、缺了怎么办"收口到一处, 与 PowerShell 侧
    Get-Sv73Cred(channels-bot/*.ps1、deploy/autopublish_verify.ps1) 对等,
    使双语言共用同一套注入约定。

读取顺序(先命中先用):
    1. 环境变量 PROD_ADMIN_PHONE / PROD_ADMIN_PASSWORD
       —— 2026-10-17 治理建立的既有约定名(见 .env.example 第七节)
    2. 环境变量 ZX_API_PHONE / ZX_API_PASSWORD
       —— PowerShell 侧同名约定, 便于一次注入双语言可用
    3. ZX_CRED_FILE 指向的仓外 JSON: {"phone": "...", "password": "..."}
    4. 全部缺失 -> raise(fail-closed, 依据 security-hard-coded 二.3)

纪律:
    - 本模块不含任何凭据字面量, 也不留明文默认值。
    - 本地内存夹具(STORE_MODE=asyncio)的种子账号单一事实源在
      repositories/store.py 的 SEED_* 常量, 仅供 dev/测试进程;
      生产 Redis 态真实账号由运维注入, 与该处无关。
    - pipe_safe=True 时收紧字符集(手机号 ^\\d+$ / 口令 ^[A-Za-z0-9]+$):
      该值会被拼进远端容器 pipe 字符串, 含 $ ` ; 即注入面(契约见
      语音智能修复Agent模块设计文档.md 的凭据字面量校验节)。
      纯 HTTP JSON 路径无需收紧, 保持 pipe_safe=False。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

_ADMIN_PHONE_NAMES = ("PROD_ADMIN_PHONE", "ZX_API_PHONE")
_PASSWORD_NAMES = ("PROD_ADMIN_PASSWORD", "ZX_API_PASSWORD")
_MEMBER_PHONE_NAMES = ("E2E_MEMBER_PHONE", "ZX_API_MEMBER_PHONE")
_CRED_FILE_FIELDS = ("phone", "password")
_RE_DIGITS = re.compile(r"^\d+$")
_RE_ALNUM = re.compile(r"^[A-Za-z0-9]+$")

_HINT = ("未配置 —— 请注入环境变量(PROD_ADMIN_* / ZX_API_*)或设 "
         "ZX_CRED_FILE 指向仓外 JSON; fail-closed 退出, 本模块不留明文默认"
         "(见 .env.example 第七节)")


def _env(names: tuple[str, ...]) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _cred_file() -> dict[str, str]:
    """ZX_CRED_FILE 回退: 仓外 JSON 凭据文件(避免口令进 shell 历史/源码)"""
    raw = (os.environ.get("ZX_CRED_FILE") or "").strip()
    if not raw:
        return {}
    path = Path(raw).expanduser()
    if not path.is_file():
        raise RuntimeError(f"ZX_CRED_FILE 指向的文件不存在: {path}")
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("ZX_CRED_FILE JSON 顶层须是对象")
    missing = [k for k in _CRED_FILE_FIELDS
               if not str(obj.get(k) or "").strip()]
    if missing:
        raise ValueError(f"ZX_CRED_FILE JSON 缺字段: {', '.join(missing)}")
    return {k: str(obj[k]).strip() for k in _CRED_FILE_FIELDS}


def _phone(names: tuple[str, ...], label: str,
           *, pipe_safe: bool) -> str:
    phone = _env(names) or _cred_file().get("phone", "")
    if not phone:
        raise RuntimeError(f"{label} {_HINT}")
    if pipe_safe and not _RE_DIGITS.match(phone):
        raise ValueError(
            f"{label} 须纯数字: 该值会拼入远端 pipe 字符串, 拒绝以防注入")
    return phone


def admin_phone(*, pipe_safe: bool = False) -> str:
    """生产站点管理员手机号(role=admin)"""
    return _phone(_ADMIN_PHONE_NAMES, "生产 admin 手机号",
                  pipe_safe=pipe_safe)


def member_phone(*, pipe_safe: bool = False) -> str:
    """E2E 普通会员手机号(权限边界对照用)"""
    return _phone(_MEMBER_PHONE_NAMES, "E2E 会员手机号",
                  pipe_safe=pipe_safe)


def login_password(*, pipe_safe: bool = False) -> str:
    """种子/E2E 账号登录口令(admin 与 member 同口令口径)"""
    pwd = _env(_PASSWORD_NAMES) or _cred_file().get("password", "")
    if not pwd:
        raise RuntimeError(f"登录口令 {_HINT}")
    if pipe_safe and not _RE_ALNUM.match(pwd):
        raise ValueError(
            "登录口令须字母数字: 该值会拼入远端 pipe 字符串, 拒绝以防注入")
    return pwd


def admin_creds(*, pipe_safe: bool = False) -> tuple[str, str]:
    """(admin 手机号, 口令) —— 生产验收脚本登录入口"""
    return (admin_phone(pipe_safe=pipe_safe),
            login_password(pipe_safe=pipe_safe))


def member_creds(*, pipe_safe: bool = False) -> tuple[str, str]:
    """(member 手机号, 口令) —— E2E 权限边界对照入口"""
    return (member_phone(pipe_safe=pipe_safe),
            login_password(pipe_safe=pipe_safe))
