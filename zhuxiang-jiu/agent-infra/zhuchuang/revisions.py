import shutil
from pathlib import Path

from contracts import DOCX_NAME, load_json


SNAPSHOT_FILES = ("manifest.json", "chapters.json", "review.json", "document.md", DOCX_NAME)


def snapshot_delivered_revision(run_dir):
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
        if all((target / name).is_file() and (target / name).read_bytes() == (run_dir / name).read_bytes() for name in SNAPSHOT_FILES):
            return target
        raise ValueError(f"历史版本已存在且内容不同，禁止覆盖：{target}")
    for name in SNAPSHOT_FILES:
        if not (run_dir / name).is_file():
            raise ValueError(f"交付快照缺少文件：{name}")
    target.mkdir(parents=True)
    for name in SNAPSHOT_FILES:
        shutil.copy2(run_dir / name, target / name)
    return target
