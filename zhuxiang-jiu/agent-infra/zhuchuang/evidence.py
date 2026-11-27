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
SECRET_PATTERN = re.compile(
    r"(?i)(authorization\s*:\s*bearer\s+\S+|api[_-]?key\s*[=:]\s*\S+|password\s*[=:]\s*\S+|token\s*[=:]\s*\S+)"
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
    return SECRET_PATTERN.sub("[已脱敏]", str(text))


def collect_evidence(repo_root, topic, source_version, limit=40):
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
