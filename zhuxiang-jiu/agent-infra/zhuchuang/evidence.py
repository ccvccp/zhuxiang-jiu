import re
import subprocess
from pathlib import Path

CANDIDATES = (
    "docs/registry/README.md",
    "docs/registry/L0-模块清单.md",
    "docs/registry/L0-页面清单.md",
    "docs/registry/L0-端点清单.md",
    "docs/registry/资产标准.md",
    "docs/registry/业务链定义.md",
    "docs/registry/红线标记.md",
    "backend/services/llm_client.py",
)
SECRET_FIELD = (
    r"(?:authorization|proxy-authorization|authentication|api[_-]?key|client[_-]?secret|"
    r"secret(?:[_-]?key)?|access[_-]?key|private[_-]?key|encryption[_-]?key|"
    r"signing[_-]?key|session[_-]?key|password|passwd|pwd|access[_-]?token|"
    r"refresh[_-]?token|id[_-]?token|auth[_-]?token|token|auth|credentials?)"
)
SECRET_VALUE = r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?!["'])[^\r\n]+)"""
SECRET_PATTERN = re.compile(
    rf"(?i)(?:(?<![A-Za-z0-9])authorization(?![A-Za-z0-9])\s*:\s*bearer\s+{SECRET_VALUE}|(?<![A-Za-z0-9]){SECRET_FIELD}(?![A-Za-z0-9])\s*[=:]\s*{SECRET_VALUE})"
)
SENSITIVE_CONFIG_PATTERN = re.compile(
    rf"(?i)(?<![A-Za-z0-9]){SECRET_FIELD}(?![A-Za-z0-9])\s*[=:]"
)


def repository_state(repo_root):
    repo_root = Path(repo_root)
    commit = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--short", "--", "."],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return {"commit": commit, "dirty": bool(status)}


def redact_text(text):
    redacted = SECRET_PATTERN.sub("[已脱敏]", str(text))
    if SENSITIVE_CONFIG_PATTERN.search(redacted):
        return ""
    return redacted


def collect_evidence(repo_root, topic, source_version, limit=40):
    if limit <= 0:
        return []
    repo_root = Path(repo_root).resolve()
    items = []
    candidates = list(CANDIDATES)
    for pattern in ("docs/*创新规划方案.md", "docs/*验收报告*.md", "docs/registry/cases/*.md"):
        candidates.extend(path.relative_to(repo_root).as_posix() for path in sorted(repo_root.glob(pattern)) if path.is_file())
    for relative in dict.fromkeys(candidates):
        path = (repo_root / relative).resolve()
        if repo_root not in path.parents or not path.is_file() or path.name.startswith(".env"):
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        selected = [index for index, line in enumerate(lines) if topic and topic in line][:3]
        if not selected:
            selected = [index for index, line in enumerate(lines) if line.strip() and not line.startswith("#")][:2]
        for index in selected:
            excerpt = redact_text(lines[index].strip())[:500]
            if not excerpt:
                continue
            items.append({
                "id": f"EV-{len(items) + 1:03d}",
                "type": "code" if relative.endswith(".py") else ("case" if "/cases/" in relative else "registry"),
                "path": relative,
                "locator": f"line={index + 1}",
                "summary": excerpt,
                "source_version": source_version,
            })
            if len(items) >= limit:
                return items
    return items
