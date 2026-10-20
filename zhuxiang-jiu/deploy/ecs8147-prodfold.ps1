# [移动端筛选折叠] products.html 三处 patch(保留备案注入与 v= 引用差异)
$ErrorActionPreference = 'Stop'
$p = 'C:\www\dist\products.html'
Copy-Item $p "$p.bak-fold-20261021" -Force
$t = [IO.File]::ReadAllText($p)

if ($t.Contains('filter-toggle-btn')) { Write-Host 'ALREADY-PATCHED'; exit 0 }

$style = @'
<style>
        /* 移动端筛选折叠(2026-10-21): 商品网格首屏优先, 筛选面板收起为按钮——
           修复移动端首屏被侧栏霸占、商品沉底一屏外的"空壳"观感 */
        .filter-toggle-btn { display: none; }
        @media (max-width: 768px) {
            .sidebar { padding: 12px; }
            .sidebar h3, .sidebar .filter-group { display: none; margin-bottom: 0; }
            .sidebar .filter-toggle-btn {
                display: block; width: 100%; padding: 12px 16px;
                background: #f0f5ef; border: 1px solid var(--color-primary, #4a7c59);
                color: var(--color-primary-dark, #2d5a3d); border-radius: 10px;
                font-size: 15px; font-family: inherit; cursor: pointer;
            }
            .sidebar.expanded h3, .sidebar.expanded .filter-group { display: block; }
        }
    </style>
    </head>
'@
$btn = @'
<aside class="sidebar">
                <button class="filter-toggle-btn" id="filterToggle" type="button">筛选 ▾</button>
                <h3>🌿 产品分类</h3>
'@
$script = @'
<script>
        // 移动端筛选折叠(2026-10-21): 点"筛选"展开面板, 再点收起; 桌面端按钮隐藏无感
        (function () {
            var btn = document.getElementById('filterToggle');
            var sb = document.querySelector('.sidebar');
            if (btn && sb) {
                btn.addEventListener('click', function () {
                    var open = sb.classList.toggle('expanded');
                    btn.textContent = open ? '收起筛选 ▴' : '筛选 ▾';
                });
            }
        })();
    </script>
    </body>
'@
# here-string 实际行尾是 CRLF(本文件 utf-8-sig 保存), 归一为 LF 与 dist 现状一致
$style = $style -replace "`r`n", "`n"
$btn = $btn -replace "`r`n", "`n"
$script = $script -replace "`r`n", "`n"

$idx = $t.IndexOf('</head>')
if ($idx -lt 0) { Write-Host 'NO-HEAD'; exit 1 }
$t = $t.Remove($idx, 7).Insert($idx, $style)

$idx = $t.IndexOf('<aside class="sidebar">')
if ($idx -lt 0) { Write-Host 'NO-ASIDE'; exit 1 }
$t = $t.Remove($idx, 23).Insert($idx, $btn)

$idx = $t.IndexOf('</body>')
if ($idx -lt 0) { Write-Host 'NO-BODY'; exit 1 }
$t = $t.Remove($idx, 7).Insert($idx, $script)

[IO.File]::WriteAllText($p, $t, [Text.UTF8Encoding]::new($false))
Write-Host 'PATCHED-OK'
# 验证三标记
$c = [IO.File]::ReadAllText($p)
Write-Host ('has_style=' + $c.Contains('filter-toggle-btn { display: none; }'))
Write-Host ('has_btn=' + $c.Contains('id="filterToggle"'))
Write-Host ('has_script=' + $c.Contains('收起筛选'))
Write-Host ('icp_kept=' + $c.Contains('zx-icp'))
Write-Host 'FOLD-DEPLOY-DONE'