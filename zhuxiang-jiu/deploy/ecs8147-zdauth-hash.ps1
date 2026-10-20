# [R2 部署前置] ECS auth_middleware.py sha256(与本地 git HEAD 版比对)
$ErrorActionPreference = 'Continue'
$sha = Get-FileHash 'C:\backend\core\auth_middleware.py' -Algorithm SHA256
Write-Host ('sha256=' + $sha.Hash)
$fi = Get-Item 'C:\backend\core\auth_middleware.py'
Write-Host ('size=' + $fi.Length)
Write-Host 'ZDAUTH-HASH-DONE'