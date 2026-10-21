# [筛选折叠 v2] IIFE 精确替换: 选定筛选项后自动收起面板回商品区
$ErrorActionPreference = 'Stop'
$p = 'C:\www\dist\products.html'
Copy-Item $p "$p.bak-fold2-20261021" -Force
$t = [IO.File]::ReadAllText($p)

$old = @'
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
'@
$new = @'
        // 移动端筛选折叠 v2(2026-10-21): 点"筛选"展开面板; 选定任一筛选项后自动收起——
        // 商品结果立即回到视口(修复展开态面板霸屏, 用户点系列后仍看不到结果的交互闭环缺陷)
        (function () {
            var btn = document.getElementById('filterToggle');
            var sb = document.querySelector('.sidebar');
            function collapse() {
                sb.classList.remove('expanded');
                btn.textContent = '筛选 ▾';
            }
            if (btn && sb) {
                btn.addEventListener('click', function () {
                    var open = sb.classList.toggle('expanded');
                    btn.textContent = open ? '收起筛选 ▴' : '筛选 ▾';
                });
                document.addEventListener('click', function (e) {
                    var t = e.target;
                    var li = t && t.closest ? t.closest('.filter-list li') : null;
                    if (li && sb.classList.contains('expanded') && window.innerWidth <= 768) {
                        collapse();
                    }
                });
            }
        })();
'@
$old = $old -replace "`r`n", "`n"
$new = $new -replace "`r`n", "`n"

if (-not $t.Contains($old)) {
    if ($t.Contains('筛选折叠 v2')) { Write-Host 'ALREADY-V2'; exit 0 }
    Write-Host 'OLD-BLOCK-NOT-FOUND'; exit 1
}
$t = $t.Replace($old, $new)
[IO.File]::WriteAllText($p, $t, [Text.UTF8Encoding]::new($false))
$c = [IO.File]::ReadAllText($p)
Write-Host ('v2_in=' + $c.Contains('筛选折叠 v2'))
Write-Host ('closest_in=' + $c.Contains('t.closest'))
Write-Host ('icp_kept=' + $c.Contains('zx-icp'))
Write-Host 'FOLD2-DEPLOY-DONE'