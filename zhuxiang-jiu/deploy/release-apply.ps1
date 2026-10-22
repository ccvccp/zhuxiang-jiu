param(
    [ValidateSet('new', 'old')][string]$Mode = 'new'
)
# [自动发布·应用] 机器侧: 拉取 latest.json → 比对本地 state → 下载校验落位 → (老机)号转换+v序列 → 验证
# new = 新机(zxjiu.com 开发)  old = 老机(zyjiu.com 生产, 受控手动/半自动)
$ErrorActionPreference = 'Continue'
$EP = 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com'
$relDir = 'C:\release'
$stateFile = "$relDir\state.json"
$dist = 'C:\www\dist'
New-Item -ItemType Directory -Path $relDir -Force | Out-Null

# 1) 拉取最新版本指针(公共读)
& curl.exe -s --max-time 30 -o "$relDir\latest.json" "$EP/deploy/release/latest.json"
$latest = Get-Content "$relDir\latest.json" -Raw -EA SilentlyContinue | ConvertFrom-Json
if (-not $latest -or -not $latest.version) { Write-Host 'RELEASE-NONE(latest.json 不可达/为空)'; exit 1 }
$cur = if (Test-Path $stateFile) { (Get-Content $stateFile -Raw | ConvertFrom-Json).version } else { '' }
Write-Host ("latest: {0}  local-state: {1}" -f $latest.version, $(if ($cur) { $cur } else { '(无)' }))
if ($latest.version -eq $cur) { Write-Host 'RELEASE-UP-TO-DATE'; exit 0 }

# 2) 下载校验
$zip = "$relDir\site.zip"
& curl.exe -s --max-time 900 -o $zip "$EP/$($latest.zip)"
$sha = (Get-FileHash $zip -Algorithm SHA256).Hash
if ($sha -ne $latest.sha256) { Write-Host "SHA-MISMATCH $sha"; exit 1 }
Write-Host 'SHA-OK'

# 3) 备份当前 dist 变更面(html/js/css)+落位
$bak = "$relDir\backup-$(Get-Date -Format 'yyyyMMdd-HHmm')"
New-Item -ItemType Directory -Path $bak -Force | Out-Null
foreach ($d in @('js', 'css', 'images')) {
    if (Test-Path "$dist\$d") { robocopy "$dist\$d" "$bak\$d" /E /NFL /NDL /NJH /NJS | Out-Null }
}
Get-ChildItem $dist -Filter *.html -File -EA SilentlyContinue | Copy-Item -Destination $bak -EA SilentlyContinue
$stage = "$relDir\stage"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
Expand-Archive $zip $stage -Force
Get-ChildItem $stage -Filter *.html -File | Copy-Item -Destination $dist -Force
foreach ($d in @('js', 'css', 'images')) {
    # /IS /IT: 强制覆盖 mtime/size 相同的文件(防同秒同名文件被跳过——v1 首发踩坑)
    if (Test-Path "$stage\$d") { robocopy "$stage\$d" "$dist\$d" /E /IS /IT /XF *.bak /NFL /NDL /NJH /NJS | Out-Null }
}
Get-ChildItem $stage -File | Where-Object { $_.Name -match '\.(ico|png|txt)$' } | Copy-Item -Destination $dist -Force -EA SilentlyContinue
Write-Host 'site applied'

# 4) 老机转换: ICP -3→-1 + main.js?v= 尾缀 a→b(独立 b 序列)
# 中文用码点拼接——脚本经云助手落位时若无 BOM, PS5 按 GBK 误读中文字面量致 Contains 失配(v2 踩坑)
$icp3 = -join ([char]0x9C81, 'ICP', [char]0x5907, '2026059492', [char]0x53F7, '-3')
$icp1 = -join ([char]0x9C81, 'ICP', [char]0x5907, '2026059492', [char]0x53F7, '-1')
if ($Mode -eq 'old') {
    $utf8 = [Text.Encoding]::UTF8
    Get-ChildItem $dist -Recurse -File | Where-Object {
        $_.Name -notmatch '\.bak' -and $_.Extension -in '.html', '.js'
    } | ForEach-Object {
        $t = [IO.File]::ReadAllText($_.FullName, $utf8)
        $dirty = $false
        if ($t.Contains($icp3)) { $t = $t.Replace($icp3, $icp1); $dirty = $true }
        $m = [regex]::Matches($t, 'main\.js\?v=(\d{8})([a-z])')
        if ($m.Count -gt 0 -and -not ($t -match 'main\.js\?v=\d{8}b')) {
            $t = [regex]::Replace($t, 'main\.js\?v=(\d{8})[a-z]', 'main.js?v=$1b')
            $dirty = $true
        }
        if ($dirty) { [IO.File]::WriteAllText($_.FullName, $t, (New-Object Text.UTF8Encoding $false)) }
    }
    Write-Host 'old-mode converted(-1 + b-series)'
}

# 5) 验证 + 更新 state
$host_ = if ($Mode -eq 'old') { 'zyjiu.com' } else { 'zxjiu.com' }
Start-Sleep -Seconds 1
$code = & curl.exe -sk -o NUL -w '%{http_code}' -m 20 "https://$host_/"
Write-Host ("verify https://{0}/ -> {1}" -f $host_, $code)
if ($code -ne '200') { Write-Host 'VERIFY-FAIL(保留 state 未推进, 可回滚 backup)'; exit 1 }
@{ version = $latest.version; applied_at = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') } |
    ConvertTo-Json -Compress | Set-Content $stateFile
Write-Host ("RELEASE-APPLIED version={0} mode={1}" -f $latest.version, $Mode)
