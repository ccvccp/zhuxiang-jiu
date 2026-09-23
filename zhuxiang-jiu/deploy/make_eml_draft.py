"""生成 Trae BOM Bug 报告邮件草稿(.eml 标准 RFC822 格式)

飞书账号未开通邮箱(无法走 lark mail), 生成通用 .eml 草稿文件:
Outlook / Windows 邮件 / Thunderbird 双击打开→核对→发送即可。
From 留空(由用户所用邮件客户端在发送时补全)。
"""
import base64
from email.message import EmailMessage
from email.utils import formatdate

HTML = """<html><body style="font-family: 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; max-width: 860px; margin: 0 auto; color: #232326; line-height: 1.65;">
<h2>[Critical / Data Corruption] Indexer repeatedly prepends UTF-8 BOM to source files during cache rebuild (v1.107.1, Windows)</h2>

<p><b>Environment</b>: Windows 11 Pro (10.0.26231) | Trae CN 1.107.1 (commit 11ec4a1, stable) | Feishu login | Project scale: ~70+ text files across Python / TypeScript / Markdown / PowerShell</p>

<h3>Summary</h3>
<p>The background indexer/cache rebuild process in Trae CN writes indexed text files back to disk with a UTF-8 BOM (<code>EF BB BF</code>). Critically, each rebuild cycle prepends an <b>additional</b> BOM layer, resulting in 2&ndash;10 stacked BOMs observed in the wild. This occurs exclusively during unattended maintenance windows (e.g., 02:53 AM) and affects only files present in the index cache &mdash; newly created sentinel files are never touched.</p>
<p>This is a <b>silent data corruption</b> issue: it breaks Python syntax (utf-8-sig strips only one BOM layer), causes parsing failures in PowerShell / TS / MD, and pollutes git working trees with dozens of unintended modifications per cycle &mdash; <b>non-user modification of source files</b>.</p>

<h3>Steps to Reproduce</h3>
<ol>
<li>Open a project with many text files (.py, .ts, .md) in Trae CN; perform normal editing and AI retrieval to populate the index cache.</li>
<li>Keep Trae CN running overnight (or wait for its automatic maintenance/update process restart).</li>
<li>The next morning, inspect the first bytes of indexed files in a binary viewer: <code>EF BB BF</code> repeated &ge;2 times. <code>git status</code> shows widespread unintended M modifications.</li>
</ol>

<h3>Evidence (systematically verified)</h3>
<ol>
<li><b>Temporal correlation</b>: Daily at 02:53:41, Trae spawns maintenance processes (<code>--aha-log-session-time=20260923T025341</code>, tsserver, NodeService). File writes begin precisely at 02:54:30 &mdash; during zero-user-activity hours.</li>
<li><b>CPU forensics</b>: During the write window, 2-second CPU delta snapshots across all processes show the Top 5 consumers are all Trae CN.exe processes (renderer +2312ms, tsserver +1343ms, node.mojom.NodeService +1313ms).</li>
<li><b>Traversal pattern</b>: 71 files' mtime advances alphabetically by directory tree at 1&ndash;3 files/sec over 5.5 continuous minutes (02:57:14 &rarr; 03:02:41) &mdash; consistent with a full index scan, not user editing.</li>
<li><b>Scope validation</b>: 14 newly created sentinel files across project directories were untouched during pollution cycles; only previously indexed files were written. This confirms the writer iterates <b>Trae's own index cache manifest</b>.</li>
<li><b>Exclusions ruled out</b>: git filter / autocrlf, Python LSP jedi (verified no-write on full reindex), Baidu Netdisk sync, scheduled tasks, and OS maintenance tasks have all been eliminated.</li>
</ol>

<h3>Impact</h3>
<ul>
<li>Python: syntax errors due to multi-layer BOM (utf-8-sig removes only one layer)</li>
<li>PowerShell / TS / MD: parsing failures and rendering issues</li>
<li>Git workflow: dozens of non-user M files appear after each rebuild cycle; requires periodic bulk <code>git checkout</code> restoration and a custom cleanup script</li>
</ul>

<h3>Expected behavior</h3>
<ol>
<li>The indexer/cache rebuild must be read-only, or strictly preserve original file bytes.</li>
<li>Provide an official cleanup tool or patched release for affected users.</li>
<li>Confirm whether a temporary toggle exists to disable background index rebuilding.</li>
</ol>

<h3>Attachments available upon request</h3>
<ul>
<li>Full forensic log package (second-level mtime distribution, raw CPU dual-snapshot data, sentinel experiment records)</li>
<li>Sample corrupted files with 2&ndash;10 stacked BOM layers</li>
<li>Git diff hex dumps showing stacked BOM patterns</li>
<li>Forensic evidence infographic</li>
</ul>

<p style="color:#787670;">Note: The same report has also been submitted via the in-IDE "Report Issue" channel (Trae CN, 2026-09-23). Sending this to the international channel as the codebase is shared.</p>
</body></html>"""

msg = EmailMessage()
msg["To"] = "feedback@mail.trae.ai"
msg["Subject"] = ("[Critical / Data Corruption] Indexer repeatedly "
                  "prepends UTF-8 BOM to source files during cache "
                  "rebuild (v1.107.1, Windows)")
msg["Date"] = formatdate(localtime=True)
msg["X-Unsent"] = "1"  # Outlook 识别为"待发送草稿"并显示发送按钮
msg.set_content("(This email requires an HTML-capable client.)")
msg.add_alternative(HTML, subtype="html")

out = r"d:\网站架构设计\Trae_BOM_Report_Draft.eml"
with open(out, "wb") as f:
    f.write(bytes(msg))
print("saved:", out, len(bytes(msg)), "bytes")
