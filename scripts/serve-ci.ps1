# ============================================================
# serve-ci.ps1 - CI Static Server (PowerShell HttpListener)
# ------------------------------------------------------------
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\serve-ci.ps1
#   Optional: -Port 8080 -Root "D:\site\zhuxiang-jiu"
# Function: Serve zhuxiang-jiu static files on localhost:8080
#   for CI pipeline (module-test.html)
# Stop: Ctrl+C
# ============================================================
param(
    [int]$Port = 8080,
    [string]$Root = ""
)

if (-not $Root) {
    $Root = Join-Path (Split-Path -Parent $PSScriptRoot) 'zhuxiang-jiu'
}

if (-not (Test-Path $Root)) {
    Write-Host "Root path not found: $Root" -ForegroundColor Red
    exit 1
}

$Root = (Resolve-Path $Root).Path.TrimEnd('\','/')

$mimeTypes = @{
    '.html' = 'text/html; charset=utf-8'
    '.js'   = 'application/javascript; charset=utf-8'
    '.css'  = 'text/css; charset=utf-8'
    '.json' = 'application/json; charset=utf-8'
    '.png'  = 'image/png'
    '.jpg'  = 'image/jpeg'
    '.svg'  = 'image/svg+xml'
    '.ico'  = 'image/x-icon'
    '.md'   = 'text/plain; charset=utf-8'
}

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://localhost:$Port/")
$listener.Start()

Write-Host "CI Server running on http://localhost:$Port/" -ForegroundColor Green
Write-Host "Root: $Root" -ForegroundColor Gray
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host ""

try {
    while ($listener.IsListening) {
        $context = $listener.GetContext()
        $req = $context.Request
        $resp = $context.Response

        $relPath = [Uri]::UnescapeDataString($req.Url.AbsolutePath).TrimStart('/')
        if ($relPath -eq '') { $relPath = 'module-test.html' }

        $fullPath = Join-Path $Root ($relPath -replace '/', '\')

        if (Test-Path $fullPath -PathType Leaf) {
            $ext = [System.IO.Path]::GetExtension($fullPath).ToLower()
            $mime = if ($mimeTypes.ContainsKey($ext)) { $mimeTypes[$ext] } else { 'application/octet-stream' }

            $bytes = [System.IO.File]::ReadAllBytes($fullPath)
            $resp.ContentType = $mime
            $resp.ContentLength64 = $bytes.Length
            $resp.OutputStream.Write($bytes, 0, $bytes.Length)
            $resp.StatusCode = 200
            Write-Host "200 $relPath" -ForegroundColor DarkGray
        } else {
            $resp.StatusCode = 404
            $body = [Text.Encoding]::UTF8.GetBytes("404 Not Found: $relPath")
            $resp.ContentType = 'text/plain; charset=utf-8'
            $resp.ContentLength64 = $body.Length
            $resp.OutputStream.Write($body, 0, $body.Length)
            Write-Host "404 $relPath" -ForegroundColor Red
        }

        $resp.Close()
    }
} finally {
    $listener.Stop()
    Write-Host "Server stopped" -ForegroundColor Yellow
}
