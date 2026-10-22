# 挂账处置: backend8147 生产 JWT_SECRET 注入(服务器端生成, 不经传输通道)
# 背景: 竹演 T1.4 硬否决发现——auth.py DEFAULT_JWT_SECRET dev 字面量, 生产 nssm
#       环境仅有 PYTHONUTF8=1, JWT_SECRET 未注入=生产实际使用 dev 默认密钥。
# 方案: 服务器端 RNG 生成 256 位 SECRET → nssm AppEnvironmentExtra 追加注入
#       (保留 PYTHONUTF8=1) → 重启服务 → 健康+注入双验证(只回长度+指纹前4位)。
# 影响: 密钥轮换后已签发 token 全部失效(会员需重新登录)——安全修复必然代价。
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
function Step($msg) { Write-Host "==> $msg" }
function Info($msg) { Write-Host "    $msg" }

Step '[1/6] 预检: 服务与当前环境'
$svc = Get-Service backend8147 -ErrorAction Stop
Info ("服务状态: " + $svc.Status)
$curEnv = cmd /c "C:\nginx\nssm.exe get backend8147 AppEnvironmentExtra" 2>&1
Info ("当前 AppEnvironmentExtra: " + ($curEnv -join ' '))
$health0 = curl.exe -s -o NUL -w '%{http_code}' -m 5 http://127.0.0.1:8000/api/health
Info ("注入前 /api/health: " + $health0)

Step '[2/6] 服务器端生成 256 位随机 SECRET(不回传明文)'
$bytes = New-Object byte[] 32
[Security.Cryptography.RNGCryptoServiceProvider]::Create().GetBytes($bytes)
$secret = ([BitConverter]::ToString($bytes)) -replace '-',''
Info ("SECRET 长度: " + $secret.Length + "  指纹前4位: " + $secret.Substring(0,4) + "****")

Step '[3/6] nssm 注入环境变量(保留 PYTHONUTF8=1)'
cmd /c "C:\nginx\nssm.exe set backend8147 AppEnvironmentExtra PYTHONUTF8=1 JWT_SECRET=$secret"
if ($LASTEXITCODE -ne 0) { throw 'nssm set 失败' }
Info '已写入 AppEnvironmentExtra'

Step '[4/6] 重启服务(密钥轮换)'
cmd /c "C:\nginx\nssm.exe restart backend8147"
Start-Sleep -Seconds 10
$state = (Get-Service backend8147).Status
Info ("重启后状态: " + $state)
if ($state -ne 'Running') {
    Info '!! 服务未起, stderr 尾部:'
    Get-Content C:\backend\logs\service-err.log -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Info $_ }
    throw '服务重启失败'
}

Step '[5/6] 健康验证(本地+公网)'
$code = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1:8000/api/health
Info ("本地 /api/health: " + $code)
$code2 = curl.exe -sk -o NUL -w '%{http_code}' -m 10 https://zxjiu.com/api/health
Info ("公网 /api/health: " + $code2)
if ($code -ne '200' -or $code2 -ne '200') { throw '健康检查未过' }

Step '[6/6] 注入核验(nssm 回读配置, 只回指纹)'
$dump = (cmd /c "C:\nginx\nssm.exe get backend8147 AppEnvironmentExtra" 2>&1) -join ' '
Info ("回读: " + ($dump -replace 'JWT_SECRET=[0-9A-F]{60,64}', 'JWT_SECRET=<64hex-已脱敏>'))
if ($dump -match 'JWT_SECRET=([0-9A-F]{4})') {
    Info ("nssm 回读指纹: " + $Matches[1] + "**** (与生成指纹一致即注入成功)")
    if ($Matches[1] -ne $secret.Substring(0,4)) { throw '指纹不一致' }
} else { throw 'nssm 回读未见 JWT_SECRET' }

Write-Host ''
Write-Host 'INJECT-DONE: JWT_SECRET 已生产注入(256位服务器端生成), 服务健康, 密钥已轮换'
