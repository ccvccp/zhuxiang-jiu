# [R2 部署收尾] 清理 ECS 临时文件
$ErrorActionPreference = 'Continue'
Remove-Item C:\tmp8147\am.zb64, C:\tmp8147\amdec.py -Force -ErrorAction SilentlyContinue
Write-Host ('am.zb64_exists=' + (Test-Path C:\tmp8147\am.zb64))
Write-Host ('amdec.py_exists=' + (Test-Path C:\tmp8147\amdec.py))
# 部署产物留存确认
Write-Host ('bak_exists=' + (Test-Path C:\backend\core\auth_middleware.py.bak-zhudun-20261021))
Write-Host 'ZD-CLEANUP-DONE'