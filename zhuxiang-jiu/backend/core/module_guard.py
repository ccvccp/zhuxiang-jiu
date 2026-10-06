"""模块保护引擎(2026-10-06, 用户定案「语音学习进化保护引擎」)

背景: 语音链(48号)经多轮真机调优趋于稳定后, 其他模块的变更
(部署重启断 WS 连接/共享文件顺手改/参数漂移)反复冲击语音链
——进化积累被连续打断, 「永远在修改的路上」。

保护模型(注册制, 任何稳定模块可纳入):

    PROTECTED_MODULES 注册表
      ├── module_id
      ├── guard_files   守卫文件清单(变更命中=必须过回归套件)
      ├── param_probe   参数指纹探针(关键常量路径列表——
      │                 import 后取值 hash 成基线, 漂移=显式
      │                 提醒, 不阻止但要求确认)
      ├── suite_cmd     回归套件命令(部署预检门禁)
      └── baseline_path 参数基线文件(首跑生成, 之后比对)

    check_changes(changed_files) -> 判定
      未命中任何守卫文件 → 放行(零负担)
      命中 → 该模块回归套件必须全绿 + 参数指纹比对报告

用法:
    部署预检: python deploy/precheck_guard.py [git-diff-files...]
    基线管理: python -m core.module_guard --rebaseline <module>

铁律: 保护=变更显式化(命中必检+漂移必报), 不阻止正当修改
——进化引擎的修改本身也走同一守卫(自洽)。
"""

import argparse
import hashlib
import importlib
import json
import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

BASELINE_DIR = Path(__file__).resolve().parent.parent / "guard_baselines"


def _probe(params: list[str]) -> dict[str, str]:
    """参数指纹: 'module.attr.sub' 路径逐级取值 → {path: str(value)}"""
    out: dict[str, str] = {}
    for spec in params:
        mod_name, _, attr_path = spec.partition(":")
        if not attr_path:
            continue
        try:
            obj = importlib.import_module(mod_name)
            val = obj
            for part in attr_path.split("."):
                val = getattr(val, part)
            out[spec] = repr(val)
        except Exception as exc:   # noqa: BLE001 探针失败记录不阻断
            out[spec] = f"<probe_failed: {exc}>"
    return out


def _fingerprint(params: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(params, sort_keys=True,
                   ensure_ascii=False).encode()).hexdigest()[:16]


# ============================================================
# 注册表(语音链 48号 首个注册者; 新模块照此格式追加)
# ============================================================

PROTECTED_MODULES: dict[str, dict] = {
    "voice48": {
        "name": "小竹语音链(48号 v97)",
        # 守卫文件: git 仓库根相对路径(与 git diff --name-only 同口径)
        "guard_files": (
            "zhuxiang-jiu/backend/services/asr_stream_service.py",
            "zhuxiang-jiu/backend/routes/xiaozhu_routes.py",
            "zhuxiang-jiu/backend/services/xiaozhu_service.py",
            "zhuxiang-jiu/backend/services/xiaozhu_evolution_service.py",
            "zhuxiang-jiu/backend/core/auth_middleware.py",  # 公开路径可选身份
            "taro-app/public/js/voice-wake-widget.js",
            "taro-app/public/xiaozhu-voice.html",
            "taro-app/public/js/resonance-beacon.js",
            "taro-app/src/index.html",           # 浮球注入清单位
        ),
        # 参数指纹探针: 纯常量(函数 repr 含地址会假漂移)
        "param_probe": (
            "services.asr_stream_service:_PACING_MAX_RATE",
            "services.asr_stream_service:_PACING_BUDGET_MS",
            "services.asr_stream_service:_FLUSH_CHUNK",
        ),
        # 回归套件(部署预检门禁; cwd=git 仓库根)
        "suite_cmd": [
            sys.executable, "-m", "pytest",
            "zhuxiang-jiu/backend/test_asr_stream.py", "-q",
        ],
        # 前端守卫(纯静态存在性: 关键标识符在位)
        "static_asserts": {
            "taro-app/public/js/voice-wake-widget.js": (
                "xz-panel-mic-ready", "pendingWakeReply",
                "inSelfReply", "handoffUntil"),
            "taro-app/public/xiaozhu-voice.html": (
                "xz-panel-mic-ready",),
        },
    },
}


# ============================================================
# 判定引擎
# ============================================================

def check_changes(changed: list[str]) -> dict:
    """变更文件清单 → 各保护模块命中判定

    Returns: {
      "clean": bool,                 # 无任何命中(直接放行)
      "hits": {module: [文件...]},
      "report": [...]                # 人读摘要行
    }
    """
    hits: dict[str, list[str]] = {}
    for mid, cfg in PROTECTED_MODULES.items():
        matched = [f for f in changed
                   if f in cfg["guard_files"]]
        if matched:
            hits[mid] = matched
    report = []
    if not hits:
        report.append("✅ 变更未触及任何保护模块——放行(零负担)")
    for mid, files in hits.items():
        report.append(
            f"⚠️ 命中保护模块 [{PROTECTED_MODULES[mid]['name']}]: "
            f"{len(files)} 个守卫文件 → 必须过回归套件+参数指纹比对")
        report.extend(f"   - {f}" for f in files)
    return {"clean": not hits, "hits": hits, "report": report}


def run_suite(module_id: str, cwd: str | None = None) -> dict:
    """跑保护模块回归套件(门禁)"""
    cmd = PROTECTED_MODULES[module_id]["suite_cmd"]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       cwd=cwd or Path(__file__).resolve().parent.parent,
                       timeout=300)
    tail = (r.stdout or "").strip().splitlines()[-3:]
    return {"ok": r.returncode == 0, "returncode": r.returncode,
            "tail": tail}


def check_static_asserts(module_id: str,
                         repo_root: Path) -> list[str]:
    """前端守卫静态断言: 关键标识符在位(误覆盖/误删检测)"""
    flaws = []
    for rel, tokens in (PROTECTED_MODULES[module_id]
                        .get("static_asserts") or {}).items():
        p = repo_root / rel
        if not p.is_file():
            flaws.append(f"{rel}: 文件缺失")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for tk in tokens:
            if tk not in text:
                flaws.append(f"{rel}: 标识符 '{tk}' 消失(误改/误删?)")
    return flaws


def check_baseline(module_id: str, repo_root: Path,
                   rebaseline: bool = False) -> dict:
    """参数指纹基线比对(漂移=提醒确认, 不阻止)"""
    cfg = PROTECTED_MODULES[module_id]
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    bp = BASELINE_DIR / f"{module_id}.json"
    # 探针跑在 backend sys.path 语境(zhuxiang-jiu/backend)
    backend = repo_root / "zhuxiang-jiu" / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    current = _probe(cfg["param_probe"])
    fp = _fingerprint(current)
    if rebaseline or not bp.is_file():
        bp.write_text(json.dumps(
            {"fingerprint": fp, "params": current},
            ensure_ascii=False, indent=1), encoding="utf-8")
        return {"status": "rebaselined", "fingerprint": fp,
                "diff": []}
    base = json.loads(bp.read_text(encoding="utf-8"))
    diff = [f"{k}: {base['params'].get(k)!r} → {v!r}"
            for k, v in current.items()
            if base["params"].get(k) != v]
    status = ("stable" if fp == base["fingerprint"]
              else "drifted" if not diff else "drifted")
    return {"status": status, "fingerprint": fp,
            "baseline": base["fingerprint"], "diff": diff}


def full_guard(changed: list[str], repo_root: Path,
               rebaseline: bool = False) -> dict:
    """部署预检主入口: 变更判定 → (命中时)套件+静态+指纹"""
    result = check_changes(changed)
    print("\n".join(result["report"]))
    if result["clean"]:
        return {"verdict": "pass", "detail": result}
    suite_ok, static_flaws, baseline = True, [], {}
    for mid in result["hits"]:
        s = run_suite(mid, cwd=str(repo_root))
        print(f"[{mid}] 回归套件: {'✅' if s['ok'] else '❌'} "
              f"{' / '.join(s['tail'][-1:])}")
        suite_ok = suite_ok and s["ok"]
        flaws = check_static_asserts(mid, repo_root)
        for f in flaws:
            print(f"[{mid}] 静态守卫 ❌ {f}")
        static_flaws += flaws
        b = check_baseline(mid, repo_root, rebaseline=rebaseline)
        if b["status"] == "drifted":
            print(f"[{mid}] 参数指纹漂移 ⚠️ "
                  f"{b['baseline']} → {b['fingerprint']}")
            for d in b["diff"]:
                print(f"   - {d}")
        elif b["status"] == "stable":
            print(f"[{mid}] 参数指纹稳定 ✓ {b['fingerprint']}")
        baseline[mid] = b
    verdict = ("pass" if suite_ok and not static_flaws else "block")
    print("=" * 56)
    print(f"预检判定: {'✅ 放行' if verdict == 'pass' else '❌ 阻止(套件红/静态守卫破)'}")
    return {"verdict": verdict, "suite_ok": suite_ok,
            "static_flaws": static_flaws, "baseline": baseline}


def main() -> None:
    ap = argparse.ArgumentParser(description="模块保护引擎")
    ap.add_argument("--rebaseline", metavar="MODULE",
                    help="重置模块参数基线(正当修改后显式确认)")
    ap.add_argument("files", nargs="*",
                    help="变更文件清单(空=仅基线查看)")
    args = ap.parse_args()
    repo_root = default_repo_root()
    if args.rebaseline:
        b = check_baseline(args.rebaseline, repo_root,
                           rebaseline=True)
        print(f"基线已重置: {args.rebaseline} → {b['fingerprint']}")
        return
    if args.files:
        full_guard(args.files, repo_root)
    else:
        for mid in PROTECTED_MODULES:
            b = check_baseline(mid, repo_root)
            print(f"{mid}: {b['status']} {b['fingerprint']}")


# git 仓库根(core/module_guard.py 上四级: core→backend→
# zhuxiang-jiu→仓库根 d:\网站架构设计)
def default_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent


if __name__ == "__main__":
    main()
