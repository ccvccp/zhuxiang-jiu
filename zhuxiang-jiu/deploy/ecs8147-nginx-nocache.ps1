# ECS nginx.conf 补 html no-cache 头(备份->改->nginx -t->reload->验证)
$ErrorActionPreference = 'Stop'
$CONF = 'C:\nginx\conf\nginx.conf'

Write-Host '==> [1/5] 备份配置'
Copy-Item $CONF 'C:\tmp8147\nginx.conf.bak-20261019'
Write-Host '    OK'

Write-Host '==> [2/5] 注入 html no-cache location(两个 server 块)'
$c = [IO.File]::ReadAllText($CONF, [Text.UTF8Encoding]::new($false))
$old = '        include site-proxy.conf;'
$new = @'
        # html 强制 no-cache(2026-10-19): 无此头时浏览器启发式缓存,
        # 部署更新到不了用户(official.html 曾长期卡旧版即此因)
        location ~ \.html$ { add_header Cache-Control "no-cache, must-revalidate"; }

        include site-proxy.conf;
'@.Replace('"\"', '"').Replace('\"', '"')
# 上面 here-string 内引号转义修正: 直接使用拼接避免转义混乱
$new = "        # html no-cache(2026-10-19): no header -> heuristic cache, deploys never reach users`r`n        location ~ \.html`$ { add_header Cache-Control `"no-cache, must-revalidate`"; }`r`n`r`n" + $old
$cnt = ([regex]::Matches($c, [regex]::Escape($old))).Count
Write-Host ('    命中 include 数: ' + $cnt)
if ($cnt -lt 1) { Write-Host '!! 未找到插入点, 中止'; exit 1 }
$c = $c.Replace($old, $new)
[IO.File]::WriteAllText($CONF, $c, [Text.UTF8Encoding]::new($false))
Write-Host '    已写入'

Write-Host '==> [3/5] nginx -t 语法检查'
& C:\nginx\nginx.exe -t 2>&1 | ForEach-Object { Write-Host ('    ' + $_) }
if ($LASTEXITCODE -ne 0) {
    Write-Host '!! 语法失败, 回滚配置'
    Copy-Item 'C:\tmp8147\nginx.conf.bak-20261019' $CONF -Force
    & C:\nginx\nginx.exe -s reload 2>&1 | Out-Null
    exit 1
}

Write-Host '==> [4/5] reload'
& C:\nginx\nginx.exe -s reload 2>&1 | ForEach-Object { Write-Host ('    ' + $_) }
Start-Sleep -Seconds 2

Write-Host '==> [5/5] 验证响应头'
$h1 = curl.exe -s -D - -o NUL -m 8 -H 'Host: zxjiu.com' http://127.0.0.1/official.html
$h1 | Select-String 'HTTP|Cache-Control' | ForEach-Object { Write-Host ('    official: ' + $_.Line.Trim()) }
$h2 = curl.exe -s -D - -o NUL -m 8 -H 'Host: zxjiu.com' http://127.0.0.1/index.html
$h2 | Select-String 'HTTP|Cache-Control' | ForEach-Object { Write-Host ('    index:    ' + $_.Line.Trim()) }
Write-Host 'NGINX-NOCACHE-PATCH-DONE'
