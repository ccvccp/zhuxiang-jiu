# [R2 部署 执行段] 拼接+解压+验证+替换+重启+探测
$ErrorActionPreference = 'Stop'
if (-not (Test-Path 'C:\tmp8147')) { New-Item -ItemType Directory -Path 'C:\tmp8147' | Out-Null }
$py = @'
import base64, zlib, io
b64 = io.open(r"C:\tmp8147\am.zb64").read()
data = zlib.decompress(base64.b64decode(b64))
io.open(r"C:\backend\core\auth_middleware.py.new", "wb").write(data)
print("written", len(data))
'@
[IO.File]::WriteAllText('C:\tmp8147\amdec.py', $py, [Text.UTF8Encoding]::new($false))
& C:\Python312\python.exe C:\tmp8147\amdec.py
if ($LASTEXITCODE -ne 0) { Write-Host 'DECODE-FAIL-ABORT'; exit 1 }
& C:\Python312\python.exe -m py_compile C:\backend\core\auth_middleware.py.new
if ($LASTEXITCODE -ne 0) { Write-Host 'COMPILE-FAIL-ABORT'; exit 1 }
Copy-Item C:\backend\core\auth_middleware.py C:\backend\core\auth_middleware.py.bak-zhudun-20261021 -Force
Move-Item C:\backend\core\auth_middleware.py.new C:\backend\core\auth_middleware.py -Force
Restart-Service backend8147
Start-Sleep -Seconds 6
$svc = Get-Service backend8147
Write-Host ('svc=' + $svc.Status)
$code = curl.exe -s -o NUL -w '%{http_code}' -m 10 'http://127.0.0.1:8000/api/decision/health'
Write-Host ('health=' + $code)
$scanCode = curl.exe -s -o NUL -w '%{http_code}' -m 10 -X POST 'http://127.0.0.1:8000/api/entry/qr/deploy-verify-20261021/scan'
Write-Host ('scanPOST=' + $scanCode)
$confCode = curl.exe -s -o NUL -w '%{http_code}' -m 10 -X POST 'http://127.0.0.1:8000/api/entry/qr/deploy-verify-20261021/confirm'
Write-Host ('confirmPOST=' + $confCode)
Write-Host 'ZD-DEPLOY-DONE'
