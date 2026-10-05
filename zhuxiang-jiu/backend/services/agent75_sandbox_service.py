"""75号·沙箱客户端与 code_tool 支撑(P3b, 2026-10-05)

backend → 沙箱容器(compose 内网/内部网络) 的执行客户端 + 审计 +
kill 开关 + 静态预检(与沙箱侧 check_code 同口径, 双端各自独立实现)。

SANDBOX_KILL=on → 所有 code_tool 拒跑, 技能级回落 prompt_tool
沙箱地址: AGENT75_SANDBOX_URL(默认 http://zhuxiang-sandbox-1:8900,
容器名 DNS——沙箱以独立 docker run 挂 --internal 网络, backend 经
`docker network connect` 活挂加入, 不重建不改编排文件)。
"""

import hashlib
import json
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

from services.agent75_orchestrator_service import AGENT_TASK_DIR

logger = logging.getLogger(__name__)

SANDBOX_URL = os.environ.get(
    "AGENT75_SANDBOX_URL", "http://zhuxiang-sandbox-1:8900").rstrip("/")


def sandbox_kill() -> bool:
    return os.environ.get(
        "AGENT75_SANDBOX_KILL", "").strip().lower() in ("on", "1", "true")


# ---------- 静态预检(backend 侧独立实现——纵深防御) ----------

ALLOWED_IMPORTS = {"json", "re", "math", "datetime", "collections",
                   "itertools", "typing"}
BANNED_NAMES = {"__import__", "eval", "exec", "compile", "open",
                "input", "breakpoint", "exit", "quit"}


def validate_code(code: str) -> tuple[bool, str]:
    """发送前预检(与沙箱 check_code 同口径; 沙箱侧仍会独立复检)"""
    import ast
    if not code or len(code.encode("utf-8")) > 128 * 1024:
        return False, "code_size"
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, f"syntax_error: {exc.msg}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                    return False, f"import_not_allowed: {a.name}"
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level == 0 and root not in ALLOWED_IMPORTS:
                return False, f"import_not_allowed: {node.module}"
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            return False, f"name_banned: {node.id}"
        elif isinstance(node, ast.Attribute) and node.attr in BANNED_NAMES:
            return False, f"attr_banned: {node.attr}"
    if not any(isinstance(n, ast.FunctionDef) and n.name == "run"
               for n in ast.walk(tree)):
        return False, "run_not_defined"
    return True, ""


# ---------- 执行客户端 ----------

def exec_code(code: str, input_data: dict,
              timeout_ms: int = 3000) -> dict[str, Any]:
    """POST /exec → {ok, result, stdout, stderr, latency_ms}; 网络层
    失败(容器不在/断网)返回 ok=False error=sandbox_unreachable"""
    payload = json.dumps({"code": code, "input": input_data,
                          "timeout_ms": timeout_ms},
                         ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{SANDBOX_URL}/exec", data=payload, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=(timeout_ms / 1000) + 5) \
                as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:   # noqa: BLE001
        logger.warning("agent75_sandbox_unreachable: %s", exc)
        return {"ok": False, "error": "sandbox_unreachable",
                "latency_ms": 0}


def healthy() -> bool:
    try:
        with urllib.request.urlopen(f"{SANDBOX_URL}/healthz",
                                    timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8")).get("ok") is True
    except Exception:   # noqa: BLE001
        return False


# ---------- 审计(方案 sandbox_runs.jsonl) ----------

def audit_run(skill_id: str, ok: bool, latency_ms: int, code: str) -> None:
    """追加审计行——不含代码全文, md5 留指纹"""
    try:
        AGENT_TASK_DIR.mkdir(parents=True, exist_ok=True)
        with open(AGENT_TASK_DIR / "sandbox_runs.jsonl", "a",
                  encoding="utf-8") as f:
            f.write(json.dumps({
                "run_id": f"r_{int(time.time() * 1000):x}",
                "skill_id": skill_id, "ok": bool(ok),
                "latency_ms": latency_ms,
                "code_md5": hashlib.md5(
                    code.encode("utf-8")).hexdigest()[:12],
                "at": int(time.time())}, ensure_ascii=False) + "\n")
    except Exception as exc:   # noqa: BLE001 审计失败不拖累主链
        logger.warning("agent75_sandbox_audit_failed: %s", exc)
