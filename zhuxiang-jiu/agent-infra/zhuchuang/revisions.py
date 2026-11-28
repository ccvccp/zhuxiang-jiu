import shutil
import tempfile
from pathlib import Path

from contracts import DOCX_NAME, load_json
from lifecycle import lifecycle_lock


SNAPSHOT_FILES = ("manifest.json", "chapters.json", "review.json", "document.md", DOCX_NAME)


def _snapshot_files_match(left, right):
    return all(
        (left / name).is_file()
        and (right / name).is_file()
        and (left / name).read_bytes() == (right / name).read_bytes()
        for name in SNAPSHOT_FILES
    )


def snapshot_delivered_revision(run_dir):
    run_dir = Path(run_dir)
    with lifecycle_lock(run_dir):
        return _snapshot_delivered_revision_unlocked(run_dir)


def _snapshot_delivered_revision_unlocked(run_dir):
    run_dir = Path(run_dir)
    manifest = load_json(run_dir / "manifest.json")
    document = manifest.get("document", {})
    if document.get("status") != "delivered":
        raise ValueError("仅允许快照已交付版本")
    revision = document.get("revision")
    if type(revision) is not int or revision < 1:
        raise ValueError("已交付版本必须有正整数版本号")
    target = run_dir / "revisions" / f"R{revision:04d}"
    if target.exists():
        if _snapshot_files_match(run_dir, target):
            return target
        raise ValueError(f"历史版本已存在且内容不同，禁止覆盖：{target}")
    for name in SNAPSHOT_FILES:
        if not (run_dir / name).is_file():
            raise ValueError(f"交付快照缺少文件：{name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent))
    try:
        for name in SNAPSHOT_FILES:
            shutil.copy2(run_dir / name, temporary / name)
        if not _snapshot_files_match(run_dir, temporary):
            raise ValueError(f"交付快照复制校验失败：{target}")
        if target.exists():
            if _snapshot_files_match(run_dir, target):
                return target
            raise ValueError(f"历史版本已存在且内容不同，禁止覆盖：{target}")
        temporary.rename(target)
        return target
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
