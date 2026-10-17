# 心境·酒签模块部署(index.html + style.css -> C:\www\dist, 含 chat E2E 验证)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Step($m) { Write-Host "==> $m" }
function Info($m) { Write-Host "    $m" }

$OSS = 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/moodwine-20261018.tgz'
$TGZ = 'C:\tmp8147\moodwine-20261018.tgz'
$BAK = 'C:\tmp8147\www-dist-bak-moodwine.tgz'
$DIST = 'C:\www\dist'
New-Item -ItemType Directory -Force C:\tmp8147 | Out-Null

Step '[1/4] 备份 index.html + style.css'
Push-Location $DIST
try { tar -czf $BAK index.html css/style.css } finally { Pop-Location }
Info ('备份: ' + [math]::Round((Get-Item $BAK).Length / 1KB, 0) + 'KB')

Step '[2/4] 下载并覆盖'
Invoke-WebRequest $OSS -OutFile $TGZ -UseBasicParsing
Info ('下载: ' + [math]::Round((Get-Item $TGZ).Length / 1KB, 0) + 'KB')
Push-Location $DIST
try { tar -xzf $TGZ -C $DIST } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Info '!! 解压失败'; exit 1 }
Info ('index.html: ' + (Get-Item "$DIST\index.html").Length + 'B; style.css: ' + (Get-Item "$DIST\css\style.css").Length + 'B')

Step '[3/4] v号归一(v=20261018, UTF8 无BOM)'
$utf8 = [Text.UTF8Encoding]::new($false)
$n = 0
Get-ChildItem "$DIST\*.html" | ForEach-Object {
    $c = [IO.File]::ReadAllText($_.FullName)
    $orig = $c
    $c = $c -replace '(css/style\.css)\?v=\d+', '$1?v=20261018'
    $c = $c -replace 'css/style\.css(")', 'css/style.css?v=20261018$1'
    if ($c -ne $orig) { [IO.File]::WriteAllText($_.FullName, $c, $utf8); $n++ }
}
Info ("改写 ${n} 个 html")
Get-ChildItem "$DIST\*.html" | ForEach-Object {
    $bytes = [IO.File]::ReadAllBytes($_.FullName)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        [IO.File]::WriteAllBytes($_.FullName, $bytes[3..($bytes.Length - 1)])
    }
}
$bomFiles = @(Get-ChildItem "$DIST\*.html" | Where-Object { [IO.File]::ReadAllBytes($_.FullName)[0] -eq 0xEF })
Info ('BOM 检查(应0): ' + $bomFiles.Count)

Step '[4/4] 验证(首页含模块 + 样式含酒签 + chat E2E)'
$homeHtml = curl.exe -s -m 8 http://127.0.0.1/index.html
$hitMood = ($homeHtml | Select-String 'moodSection' -Quiet)
$hitWidget = ($homeHtml | Select-String 'moodTags' -Quiet)
$css = curl.exe -s -m 8 'http://127.0.0.1/css/style.css?v=20261018'
$hitCss = ($css | Select-String 'mood-card' -Quiet)
Info ("首页含 moodSection: $hitMood / moodTags: $hitWidget / css 含 mood-card: $hitCss")
if (-not $hitMood -or -not $hitCss) { Info '!! 静态验证未过'; exit 1 }

# E2E: 模拟首页酒签调用链(游客无鉴权打公开端点 /api/mood/wine)
try {
    $q = [Text.Encoding]::UTF8.GetBytes('{"mood":"今晚想一个人小酌放松，帮我推荐一款适合的竹奕酒？"}')
    $r = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1/api/mood/wine' -ContentType 'application/json' -Body $q -TimeoutSec 30
    $reply = $r.data.reply
    Info ('mood E2E: reply 长度 ' + $reply.Length + ' / mode ' + $r.data.mode + ' / 置信 ' + $r.data.confidence)
    if (-not $reply) { Info '!! reply 为空'; exit 1 }
} catch {
    Info ('!! mood E2E 异常: ' + $_.Exception.Message)
    exit 1
}
Write-Host 'MOODWINE-DEPLOY-DONE'
