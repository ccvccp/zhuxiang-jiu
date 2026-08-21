# 竹香酒官网 - 本地静态文件服务器（PowerShell）
# 基于 .NET HttpListener，无需 Python/Node 依赖

$port = 8080
$root = $PSScriptRoot
Add-Type -AssemblyName System.Web

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://localhost:$port/")
$listener.Start()

Write-Host "============================================" -ForegroundColor Green
Write-Host "  竹香酒官网本地预览服务已启动" -ForegroundColor Green
Write-Host "  访问地址: http://localhost:$port/" -ForegroundColor Yellow
Write-Host "  网站根目录: $root" -ForegroundColor Gray
Write-Host "  按 Ctrl+C 停止服务" -ForegroundColor Gray
Write-Host "============================================" -ForegroundColor Green
Write-Host ""

$mimeMap = @{
    '.html' = 'text/html; charset=utf-8'
    '.htm'  = 'text/html; charset=utf-8'
    '.css'  = 'text/css; charset=utf-8'
    '.js'   = 'application/javascript; charset=utf-8'
    '.json' = 'application/json; charset=utf-8'
    '.png'  = 'image/png'
    '.jpg'  = 'image/jpeg'
    '.jpeg' = 'image/jpeg'
    '.gif'  = 'image/gif'
    '.svg'  = 'image/svg+xml'
    '.ico'  = 'image/x-icon'
    '.woff' = 'font/woff'
    '.woff2'= 'font/woff2'
    '.ttf'  = 'font/ttf'
    '.map'  = 'application/json'
}

while ($listener.IsListening) {
    try {
        $context = $listener.GetContext()
    } catch {
        break
    }

    $request = $context.Request
    $response = $context.Response

    $url = $request.Url.LocalPath
    if ($url -eq '/' -or $url -eq '') { $url = '/index.html' }

    $filePath = Join-Path $root $url.Substring(1)
    $filePath = [System.Web.HttpUtility]::UrlDecode($filePath)

    $logTime = Get-Date -Format "HH:mm:ss"
    if (Test-Path $filePath -PathType Leaf) {
        $ext = [System.IO.Path]::GetExtension($filePath).ToLower()
        $contentType = if ($mimeMap.ContainsKey($ext)) { $mimeMap[$ext] } else { 'application/octet-stream' }
        $bytes = [System.IO.File]::ReadAllBytes($filePath)
        $response.ContentType = $contentType
        $response.ContentLength64 = $bytes.Length
        $response.OutputStream.Write($bytes, 0, $bytes.Length)
        Write-Host "[$logTime] 200  $url" -ForegroundColor Green
    } else {
        $msg = "<h1>404 Not Found</h1><p>文件未找到: $url</p><p><a href='/'>返回首页</a></p>"
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($msg)
        $response.StatusCode = 404
        $response.ContentType = 'text/html; charset=utf-8'
        $response.ContentLength64 = $bytes.Length
        $response.OutputStream.Write($bytes, 0, $bytes.Length)
        Write-Host "[$logTime] 404  $url" -ForegroundColor Red
    }

    $response.OutputStream.Close()
}

$listener.Stop()
