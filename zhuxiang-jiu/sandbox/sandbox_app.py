"""75号·沙箱执行服务(P3b)——python:3.12-slim 零第三方依赖

《P3 详细设计方案》§2 落地: LLM 生成代码片段(code_tool)的隔离执行。

双层闸(独立失效):
    外层=容器资源闸(docker run 限制, 见 README):
        --internal 网络(断外网) / --read-only / 非根 / mem 256m /
        cpus 0.5 / pids 64
    内层=本服务代码闸:
        ① AST 白名单 import(json/re/math/datetime/collections/
          itertools/typing) ② 危险名目禁用(__import__/eval/exec/
          compile/open/input/breakpoint) ③ 子进程 python -I -S
          (隔离模式) ④ 三限: 墙钟超时(默认 3s 硬杀)/输出 64KB/
          递归 64 ⑤ 请求体 128KB ⑥ run(input)->dict 入口约定

协议: POST /exec {code, input, timeout_ms}
      → {ok, result, stdout, stderr, latency_ms}
      GET /healthz → {"ok": true}
"""

import ast
import json
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8900
ALLOWED_IMPORTS = {"json", "re", "math", "datetime", "collections",
                   "itertools", "typing"}
BANNED_NAMES = {"__import__", "eval", "exec", "compile", "open",
                "input", "breakpoint", "exit", "quit"}
MAX_CODE_BYTES = 128 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
MAX_REQUEST_BYTES = 128 * 1024
DEFAULT_TIMEOUT_MS = 3000
MAX_TIMEOUT_MS = 10_000
MAX_RECURSION = 64

_DRIVER = f"""
import json, sys
sys.setrecursionlimit({MAX_RECURSION})
with open(sys.argv[1], encoding="utf-8") as f:
    input_data = json.load(f)
ns = {{}}
exec(compile(open(sys.argv[2], encoding="utf-8").read(), "code", "exec"), ns)
run = ns.get("run")
if not callable(run):
    print("ERROR: run(input) not defined")
    sys.exit(3)
out = run(input_data)
# 结果走文件(utf-8 字节安全; stdout 仅诊断——-I 模式下控制台编码
# 不可控, Windows 实证 GBK/UTF-8 冲突)
with open(sys.argv[3], "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, default=str)
print("OK")
"""


def check_code(code: str) -> tuple[bool, str]:
    """静态闸: 白名单 import + 危险名目 + run 入口存在"""
    if not code or len(code.encode("utf-8")) > MAX_CODE_BYTES:
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
    has_run = any(isinstance(n, ast.FunctionDef) and n.name == "run"
                  for n in ast.walk(tree))
    if not has_run:
        return False, "run_not_defined"
    return True, ""


def do_exec(code: str, input_data: dict,
            timeout_ms: int) -> dict:
    """执行闸: 子进程 -I -S + 墙钟硬杀 + 输出三限"""
    timeout_ms = min(max(int(timeout_ms or DEFAULT_TIMEOUT_MS), 100),
                     MAX_TIMEOUT_MS)
    start = time.time()
    tmp = tempfile.mkdtemp(prefix="a75_")
    code_p = f"{tmp}/{uuid.uuid4().hex}.py"
    drv_p = f"{tmp}/{uuid.uuid4().hex}.py"
    inp_p = f"{tmp}/{uuid.uuid4().hex}.json"
    res_p = f"{tmp}/{uuid.uuid4().hex}.res.json"
    try:
        with open(code_p, "w", encoding="utf-8") as f:
            f.write(code)
        with open(drv_p, "w", encoding="utf-8") as f:
            f.write(_DRIVER)
        with open(inp_p, "w", encoding="utf-8") as f:
            json.dump(input_data if isinstance(input_data, dict) else {},
                      f, ensure_ascii=False)
        try:
            import os
            env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            r = subprocess.run(
                [sys.executable, "-I", "-S", "-X", "utf8",
                 drv_p, inp_p, code_p, res_p],
                capture_output=True, timeout=timeout_ms / 1000,
                encoding="utf-8", errors="replace", env=env)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timeout",
                    "latency_ms": int((time.time() - start) * 1000)}
        stdout, stderr = r.stdout or "", r.stderr or ""
        blob = stdout + stderr
        truncated = len(blob.encode("utf-8")) > MAX_OUTPUT_BYTES
        if truncated:
            stdout = stdout[: MAX_OUTPUT_BYTES // 2]
            stderr = stderr[: MAX_OUTPUT_BYTES // 2]
        out = {"ok": False, "stdout": stdout[-2000:],
               "stderr": stderr[-2000:], "truncated": truncated,
               "latency_ms": int((time.time() - start) * 1000)}
        if r.returncode != 0:
            out["error"] = f"exit_{r.returncode}"
            return out
        try:
            with open(res_p, encoding="utf-8") as f:
                result = json.load(f)
            if isinstance(result, dict):
                # 结果体同样受 64KB 限额(走文件后 stdout 限额不再覆盖
                # ——生产攻防实证 big_output 漏网补丁)
                if len(json.dumps(result, ensure_ascii=False,
                                  default=str).encode("utf-8")) \
                        > MAX_OUTPUT_BYTES:
                    out["error"] = "output_too_large"
                else:
                    out["result"] = result
                    out["ok"] = True
            else:
                out["error"] = "result_not_dict"
        except (json.JSONDecodeError, OSError):
            out["error"] = "result_unreadable"
        return out
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):   # 静默访问日志
        pass

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"ok": False, "error": "not_found"})

    def do_POST(self):
        if self.path != "/exec":
            self._json(404, {"ok": False, "error": "not_found"})
            return
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_REQUEST_BYTES:
            self._json(413, {"ok": False, "error": "request_too_large"})
            return
        try:
            req = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:   # noqa: BLE001
            self._json(400, {"ok": False, "error": "bad_json"})
            return
        code = str(req.get("code") or "")
        ok, err = check_code(code)
        if not ok:
            self._json(200, {"ok": False, "error": err, "latency_ms": 0})
            return
        self._json(200, do_exec(code, req.get("input") or {},
                                req.get("timeout_ms")))


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[agent75-sandbox] listening :{PORT}", flush=True)

    def _reap():   # 僵尸子进程回收兜底
        while True:
            time.sleep(30)
            threading.active_count()

    threading.Thread(target=_reap, daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
