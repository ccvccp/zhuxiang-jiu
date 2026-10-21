# [筛选重构 v3 chips·行级] products.html 按标记行定位 patch + index.html ver=2
$ErrorActionPreference = 'Stop'
$p = 'C:\www\dist\products.html'
Copy-Item $p "$p.bak-v3-20261021" -Force
$t = [IO.File]::ReadAllText($p)
if ($t.Contains('series-chips')) { Write-Host 'ALREADY-V3'; exit 0 }
$lines = [System.Collections.Generic.List[string]]::new()
[IO.File]::ReadAllLines($p) | ForEach-Object { [void]$lines.Add($_) }

# --- a. v1 style 块( <style> 行起至 </style> 行止, 含'筛选折叠'标记) ---
$s = -1; $e = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '<style>' -and $s -lt 0) { $s = $i }
    if ($lines[$i] -match '</style>' -and $s -ge 0) { $e = $i; break }
}
if ($s -lt 0 -or $e -le $s) { Write-Host 'STYLE-RANGE-NOT-FOUND'; exit 1 }
Write-Host ("style_range: " + $s + "-" + $e)
$v3style = @'
    <style>
        /* 移动端筛选重构 v3(2026-10-21): 干掉侧栏面板概念——系列 chips 横滑条常驻商品上方,
           点选即筛同屏可见(结构上杜绝"点了没反应"); 桌面端保留原侧栏零改动 */
        .series-chips { display: none; }
        @media (max-width: 768px) {
            .sidebar { display: none !important; }
            .series-chips {
                display: flex !important;
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
                gap: 8px;
                padding: 10px 0 4px;
                scrollbar-width: none;
            }
            .series-chips::-webkit-scrollbar { display: none; }
        }
        .series-chips .chip {
            flex: 0 0 auto;
            padding: 8px 16px;
            border-radius: 20px;
            background: #f0f5ef;
            border: 1px solid var(--color-primary, #4a7c59);
            font-size: 14px;
            color: var(--color-primary-dark, #2d5a3d);
            cursor: pointer;
            user-select: none;
        }
        .series-chips .chip.active {
            background: var(--color-primary, #4a7c59);
            color: #fff;
            border-color: var(--color-primary, #4a7c59);
        }
    </style>
'@ -split "`n"
$lines.RemoveRange($s, $e - $s + 1)
$lines.InsertRange($s, [string[]]$v3style)

# --- b. 删除 v1 按钮行(含 filter-toggle-btn 的 <button 行) ---
for ($i = $lines.Count - 1; $i -ge 0; $i--) {
    if ($lines[$i] -match '<button class="filter-toggle-btn"') { $lines.RemoveAt($i); break }
}

# --- c. main 开头插 chips ---
$chips = @'
            <main class="products-main">
                <!-- 移动端系列 chips 横滑条(v3): 点选即筛, 与商品同屏 -->
                <div class="series-chips" id="seriesChips">
                    <span class="chip active" data-series="all">全部</span>
                    <span class="chip" data-series="竹香系列">竹香</span>
                    <span class="chip" data-series="经典系列">经典</span>
                    <span class="chip" data-series="珍藏系列">珍藏</span>
                    <span class="chip" data-series="年份系列">年份</span>
                    <span class="chip" data-series="礼盒系列">礼盒</span>
                    <span class="chip" data-series="便携系列">便携</span>
                    <span class="chip" data-series="典藏系列">典藏</span>
                </div>
'@ -split "`n"
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '<main class="products-main">') {
        $lines.RemoveAt($i)
        $lines.InsertRange($i, [string[]]$chips)
        break
    }
}

# --- d. v2 IIFE 块( '筛选折叠 v2' 注释行起至 '})();' 行止) ---
$s = -1; $e = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '筛选折叠 v2') { $s = $i; break }
}
if ($s -ge 0) {
    for ($i = $s; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '}\)\(\);') { $e = $i; break }
    }
    if ($e -gt $s) {
        Write-Host ("iife_range: " + $s + "-" + $e)
        $v3iife = @'
        // 移动端系列 chips v3(2026-10-21): 点选即筛+同步侧栏 active 态(桌面共用 filterState)
        (function () {
            var chips = document.getElementById('seriesChips');
            if (!chips) return;
            chips.addEventListener('click', function (e) {
                var t = e.target;
                var chip = t && t.closest ? t.closest('.chip') : null;
                if (!chip) return;
                chips.querySelectorAll('.chip').forEach(function (c) { c.classList.remove('active'); });
                chip.classList.add('active');
                filterState.series = chip.dataset.series;
                renderList();
                document.querySelectorAll('#seriesFilter li').forEach(function (li) {
                    li.classList.toggle('active', li.dataset.series === chip.dataset.series);
                });
            });
        })();
'@ -split "`n"
        $lines.RemoveRange($s, $e - $s + 1)
        $lines.InsertRange($s, [string[]]$v3iife)
    } else { Write-Host 'IIFE-END-NOT-FOUND'; exit 1 }
} else { Write-Host 'IIFE-START-NOT-FOUND'; exit 1 }

[IO.File]::WriteAllText($p, ($lines -join "`n"), [Text.UTF8Encoding]::new($false))
$c = [IO.File]::ReadAllText($p)
Write-Host ('v3_style=' + $c.Contains('系列 chips 横滑条常驻商品上方'))
Write-Host ('chips_html=' + $c.Contains('id="seriesChips"'))
Write-Host ('iife_v3=' + $c.Contains('chips v3'))
Write-Host ('btn_removed=' + (-not $c.Contains('filterToggle')))
Write-Host ('style_v1_gone=' + (-not $c.Contains('filter-toggle-btn {')))
Write-Host ('icp_kept=' + $c.Contains('zx-icp'))

# --- index.html 两处链接 ver=2 ---
$ix = 'C:\www\dist\index.html'
Copy-Item $ix "$ix.bak-v3-20261021" -Force
$it = [IO.File]::ReadAllText($ix)
$it = $it.Replace('href="products.html"', 'href="products.html?ver=2"')
$it = $it.Replace("url: 'products.html'", "url: 'products.html?ver=2'")
[IO.File]::WriteAllText($ix, $it, [Text.UTF8Encoding]::new($false))
$ic = [IO.File]::ReadAllText($ix)
Write-Host ('idx_ver2=' + (($ic.Split('products.html?ver=2').Count - 1)))
Write-Host 'PRODV3-DONE'