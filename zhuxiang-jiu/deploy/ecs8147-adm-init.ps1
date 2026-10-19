﻿﻿﻿# 生产正式超管初始化(替代原 seed 替换: 生产 admin 域零账号, AUTH_MODE=strict)
# 路径: 代码层直调 AdminService(不走 HTTP 建号面) -> 环回 login 验证 -> 凭证仅输出一次
$ErrorActionPreference = 'Continue'
$T = 'C:\tmp8147\arch'; New-Item -ItemType Directory -Force $T | Out-Null
function Info($m) { Write-Host "    $m" }

function New-StrongPassword {
    $upper = 'ABCDEFGHJKLMNPQRSTUVWXYZ'; $lower = 'abcdefghijkmnopqrstuvwxyz'
    $digits = '23456789'; $special = '!@#$%^&*-_=+'
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    function Pick([string]$set, [int]$n) {
        $bytes = New-Object byte[] $n; $rng.GetBytes($bytes)
        -join ($bytes | ForEach-Object { $set[$_ % $set.Length] })
    }
    (Pick $upper 4) + (Pick $lower 6) + (Pick $digits 4) + (Pick $special 2)
}

Write-Host '== [1/4] 生成强密码(不落 git, 临时文件即删) =='
$pwdZx = New-StrongPassword
[IO.File]::WriteAllText("$T\pwd.txt", $pwdZx)
Info ('密码长度: ' + $pwdZx.Length)

Write-Host '== [2/4] 写初始化 py(ASCII-only 规避通道坑) =='
$py = @'
import asyncio, sys, os
sys.path.insert(0, r'C:\backend')
# py 不加载 .env, 须手动对齐服务口径: RESP2 + redis 存储模式
_base = os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379/0').split('?')[0]
os.environ['REDIS_URL'] = _base + '?protocol=2'
os.environ['STORE_MODE'] = 'redis'
os.environ['LOCK_MODE'] = 'redis'
from services.admin_service import AdminService

async def main():
    svc = AdminService()
    pwd = open(r'C:\tmp8147\arch\pwd.txt').read().strip()
    role = await svc.repo.get_role_by_code('super_admin')
    if role is None:
        role = await svc.create_role('super_admin', 'SuperAdmin', 'prod root role')
    rid = role['id']
    u = await svc.create_user('zx_admin', pwd, real_name='prod-admin',
                              department='ops', position='root', role_ids=[rid])
    print('CREATED userId=%s roleId=%s roleCode=%s' % (u['id'], rid, role['roleCode']))

asyncio.run(main())
'@
[IO.File]::WriteAllText("$T\init_admin.py", $py)
Info 'py 已写'

Write-Host '== [3/4] 执行建号(cwd=C:\backend 规避相对导入) =='
Push-Location C:\backend
$out = & C:\Python312\python.exe "$T\init_admin.py" 2>&1 | Out-String
Pop-Location
Info ('py out: ' + $out.Trim())
if ($out -notmatch 'CREATED userId=') { Info '!! 建号失败中止'; Remove-Item "$T\pwd.txt","$T\init_admin.py" -Force -ErrorAction SilentlyContinue; exit 1 }

Write-Host '== [4/4] 环回 login 验证 =='
$lp = "$T\login.json"
('{"username":"zx_admin","password":"' + $pwdZx + '"}') | Out-File -FilePath $lp -Encoding ascii
$r = curl.exe -s -m 15 -X POST http://127.0.0.1:8000/api/admin/login -H 'Content-Type: application/json' --data-binary "@$lp"
Info ('login: ' + $r.Substring(0, [Math]::Min(220, $r.Length)))
Remove-Item $lp -Force -ErrorAction SilentlyContinue
if ($r -notmatch '"success":true') { Info '!! login 验证未过——账号已建, 请人工检查!'; exit 1 }
if ($r -match '"mustChangePassword":true') { Info '(mustChange 标记存在——因非默认密码不应出现, 请人工核对)' }
Remove-Item "$T\pwd.txt","$T\init_admin.py" -Force -ErrorAction SilentlyContinue

Write-Host ''
Write-Host '================ 生产超管凭证(仅显示一次, 立即保存) ================'
Write-Host ('  用户名: zx_admin')
Write-Host ('  密  码: ' + $pwdZx)
Write-Host '  登录端点: POST /api/admin/login (ICP 放开后经 https 域名访问)'
Write-Host '====================================================================='
Write-Host 'ADM-INIT-DONE'
