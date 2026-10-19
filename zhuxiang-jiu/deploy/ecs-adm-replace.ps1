﻿﻿﻿# 生产 seed 管理员替换(云助手执行体: 经 aliyun-ecs-rpc.ps1 Send 投递到国内 ECS,
# 环回 127.0.0.1:8000 调后端——公网被 ICP 备案阻断, 环回不受拦截)
# 背景: seed 管理员 admin/admin123 弱口令(P0-4), 且默认密码会话带
# mustChangePassword 陷阱(除改密端点全 403)——故流程必须先改密再操作。
# 七步流(任一步失败即停, 半途最坏态=旧号已改密可恢复):
#   1 双强密码生成(16位四类) -> 2 旧号改密 -> 3 新密码重登 -> 4 超管角色反查
#   -> 5 建 zx_admin 新号 -> 6 新号验证 -> 7 新号会话禁用旧号
# 凭证仅本脚本输出一次, 投递方须立即保存。
$ErrorActionPreference = 'Stop'
$API = 'http://127.0.0.1:8000'

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

function Api([string]$Method, [string]$Path, $Body, $Token) {
    $h = @{}
    if ($Token) { $h['X-Admin-Token'] = $Token; $h['X-Role'] = 'admin' }
    $json = if ($Body) { $Body | ConvertTo-Json -Depth 5 } else { $null }
    Invoke-RestMethod -Method $Method -Uri "$API$Path" -Headers $h `
        -ContentType 'application/json' -Body $json -TimeoutSec 30
}

function Fail([string]$Step) { Write-Host "!! 步骤失败即停: $Step"; exit 1 }

Write-Host '== [1/7] 生成双强密码 =='
$pwdOld2New = New-StrongPassword   # admin 旧号新密码
$pwdZxAdmin = New-StrongPassword   # zx_admin 新号密码

Write-Host '== [2/7] 旧号登录并改密(mustChangePassword 会话仅可改本人) =='
try {
    $r1 = Api 'POST' '/api/admin/login' @{ username = 'admin'; password = 'admin123' } $null
} catch { Fail ("旧口令登录失败(可能已被替换或锁定): " + $_.Exception.Message) }
if (-not $r1.success) { Fail '旧口令登录失败' }
$tok1 = $r1.data.sessionToken; $uid1 = $r1.data.userId
Write-Host ("    admin userId={0} mustChangePassword={1}" -f $uid1, $r1.data.mustChangePassword)
$r2 = Api 'POST' "/api/admin/users/$uid1/reset-password" @{ newPassword = $pwdOld2New } $tok1
if (-not $r2.success) { Fail '旧号改密失败' }
Write-Host '    admin 改密 OK(须改密标记已随改密解除)'

Write-Host '== [3/7] 新密码重登 admin(取完整权限会话) =='
$r3 = Api 'POST' '/api/admin/login' @{ username = 'admin'; password = $pwdOld2New } $null
if (-not $r3.success) { Fail 'admin 新密码重登失败——凭证已换, 请用下方新口令人工恢复!' }
$tokA = $r3.data.sessionToken
Write-Host '    admin 新密码会话 OK'

Write-Host '== [4/7] 超管角色反查 =='
$r4 = Api 'GET' '/api/admin/roles?limit=200' $null $tokA
if (-not $r4.success) { Fail '角色列表查询失败' }
$roles = $r4.data
if ($roles -isnot [System.Array]) { $roles = @($roles) }
$super = $roles | Where-Object {
    ($_.roleCode -match 'super|admin_root') -or ($_.roleName -match '超级|超管') } |
    Select-Object -First 1
if (-not $super) { Fail '未找到超管角色(roleCode 含 super/admin_root)' }
$superRoleId = if ($super.roleId) { $super.roleId } else { $super.id }
Write-Host ("    超管角色: {0}({1}) roleId={2}" -f $super.roleCode, $super.roleName, $superRoleId)

Write-Host '== [5/7] 建 zx_admin 新号(绑超管角色) =='
$r5 = Api 'POST' '/api/admin/users' @{
    username = 'zx_admin'; password = $pwdZxAdmin
    realName = '生产管理员'; department = '运维'; roleIds = @([int]$superRoleId)
} $tokA
if (-not $r5.success) { Fail ('建号失败: ' + ($r5 | ConvertTo-Json -Depth 3)) }
$uidZ = if ($r5.data.userId) { $r5.data.userId }
        elseif ($r5.data.id) { $r5.data.id } else { $null }
Write-Host ("    zx_admin 建号 OK userId={0}" -f $uidZ)

Write-Host '== [6/7] 新号验证登录 =='
$r6 = Api 'POST' '/api/admin/login' @{ username = 'zx_admin'; password = $pwdZxAdmin } $null
if (-not $r6.success) { Fail 'zx_admin 登录失败——请人工检查!' }
$tokZ = $r6.data.sessionToken
Write-Host '    zx_admin 登录 OK'

Write-Host '== [7/7] 新号会话禁用旧号 admin =='
$r7 = Api 'PUT' "/api/admin/users/$uid1" @{ status = 'disabled' } $tokZ
if (-not $r7.success) { Fail '禁用旧号失败(可人工处理, 新号已可用)' }
Write-Host '    admin 旧号已禁用 OK'

Write-Host ''
Write-Host '================ 生产管理员凭证(仅显示一次, 立即保存) ================'
Write-Host ("  新超管: zx_admin / {0}" -f $pwdZxAdmin)
Write-Host ("  旧号(admin, 已禁用): {0}" -f $pwdOld2New)
Write-Host '====================================================================='
Write-Host 'ADM-REPLACE-DONE'
