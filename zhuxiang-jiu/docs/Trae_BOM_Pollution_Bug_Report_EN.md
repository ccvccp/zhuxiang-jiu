# Bug Report: Trae CN Indexer Repeatedly Prepends UTF-8 BOM to Source Files During Cache Rebuild

> 投递渠道: feedback@mail.trae.ai（国际版）／ forum.trae.ai Issues 板块
> 中文版已通过 Trae CN IDE 内「报告问题」渠道提交（2026-09-23）

---

**Title**: [Critical / Data Corruption] Indexer repeatedly prepends UTF-8 BOM to source files during cache rebuild (v1.107.1, Windows)

**Environment**:

- OS: Windows 11 Pro (10.0.26231)
- Trae CN Version: 1.107.1 (commit 11ec4a1, stable channel)
- Account: Feishu login
- Project Scale: ~70+ text files across Python / TypeScript / Markdown / PowerShell

**Summary**:

The background indexer/cache rebuild process in Trae CN writes indexed text files back to disk with a UTF-8 BOM (`EF BB BF`). Critically, each rebuild cycle prepends an **additional** BOM layer, resulting in 2–10 stacked BOMs observed in the wild. This occurs exclusively during unattended maintenance windows (e.g., 02:53 AM) and affects only files present in the index cache — newly created sentinel files are never touched.

This is a **silent data corruption** issue: it breaks Python syntax (utf-8-sig strips only one BOM layer), causes parsing failures in PowerShell / TS / MD, and pollutes git working trees with dozens of unintended modifications per cycle — **non-user modification of source files**.

**Steps to Reproduce**:

1. Open a project with many text files (.py, .ts, .md) in Trae CN; perform normal editing and AI retrieval to populate the index cache.
2. Keep Trae CN running overnight (or wait for its automatic maintenance/update process restart).
3. The next morning, inspect the first bytes of indexed files in a binary viewer: `EF BB BF` repeated ≥2 times. `git status` shows widespread unintended `M` modifications.

**Evidence (systematically verified)**:

1. **Temporal correlation**: Daily at 02:53:41, Trae spawns maintenance processes (`--aha-log-session-time=20260923T025341`, tsserver, NodeService). File writes begin precisely at 02:54:30 — during zero-user-activity hours.
2. **CPU forensics**: During the write window, 2-second CPU delta snapshots across all processes show the Top 5 consumers are all Trae CN.exe processes (renderer +2312ms, tsserver +1343ms, node.mojom.NodeService +1313ms).
3. **Traversal pattern**: 71 files' mtime advances alphabetically by directory tree at 1–3 files/sec over 5.5 continuous minutes (02:57:14 → 03:02:41) — consistent with a full index scan, not user editing.
4. **Scope validation**: 14 newly created sentinel files across project directories were untouched during pollution cycles; only previously indexed files were written. This confirms the writer iterates **Trae's own index cache manifest**.
5. **Exclusions ruled out**: git filter / autocrlf, Python LSP jedi (verified no-write on full reindex), Baidu Netdisk sync, scheduled tasks (LargeFileClean / WXClean are orphaned after uninstall), and OS maintenance tasks have all been eliminated.

**Impact**:

- Python: syntax errors due to multi-layer BOM (utf-8-sig removes only one layer)
- PowerShell / TS / MD: parsing failures and rendering issues
- Git workflow: dozens of non-user `M` files appear after each rebuild cycle; requires periodic bulk `git checkout` restoration and a custom cleanup script

**Expected behavior**:

1. The indexer/cache rebuild must be read-only, or strictly preserve original file bytes.
2. Provide an official cleanup tool or patched release for affected users.
3. Confirm whether a temporary toggle exists to disable background index rebuilding.

**Attachments available upon request**:

- Full forensic log package (second-level mtime distribution, raw CPU dual-snapshot data, sentinel experiment records)
- Sample corrupted files with 2–10 stacked BOM layers
- Git diff hex dumps showing stacked BOM patterns
- Forensic evidence infographic (see: zhuxiang-jiu/docs/BOM污染取证_证据总览图.png in the referenced repository)
