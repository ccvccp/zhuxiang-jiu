# t4-B 生产登录凭据注入(ECS backend 服务侧) —— 交互式, 必须在 ECS 的 RDP 会话里以【管理员】身份本地运行
# 用法: 先把本脚本拷到 ECS 任意目录(如 C:\release\), 再运行
#   powershell -ExecutionPolicy Bypass -File C:\release\setup-prod-creds-ecs-service.ps1
# 【不可走云助手 RunCommand】本脚本含 Read-Host 交互; 且口令不得经云助手命令/OSS 中转/任何日志传输
#   (依据 .trae/rules/security-hard-coded.md 一.2 + 用户裁定"口令不下发, 我自己在 ECS 上设")
#
# 为什么是"服务侧"而不是"容器侧": 2026-11-09 实测该 ECS(i-2zebz5dnx9903xdsbi7m)【没有 Docker】
#   (6 个 docker.exe 候选路径全 False / com.docker.service=ABSENT / 无 docker 进程 / PATH 无 docker),
#   backend 是 nssm 托管的 Windows 服务直跑宿主机 Python:
#     svc=backend8147  PathName=C:\nginx\nssm.exe
#     pid cmd = "C:\Python312\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000
#               --env-file C:\backend\.env
#   ⇒ env 注入点是 C:\backend\.env(【不是】compose 根 .env), 且 env 只在服务启动时读一次,
#     改完必须 Restart-Service 才生效; 机器级 env 变更对已运行进程不可见。
#   另: C:\backend\.git=False(发布产物副本, 非 git 工作区) ⇒ 写 .env 不存在误提交风险。
# 产物: 往 C:\backend\.env 就地替换(或追加) PROD_ADMIN_PHONE 与 PROD_ADMIN_PASSWORD
# 幂等: 已存在的键就地替换, 不产生重复键; 覆盖前先备份 .env(依据 二.4 备份先于写入)
param(
    [string]$EnvFile = 'C:\backend\.env',
    [string]$ServiceName = 'backend8147',
    [int]$Port = 8000,
    [switch]$NoRestart
)
$ErrorActionPreference = 'Continue'

function Fail([string]$msg) {
    Write-Host ('  [FAIL] ' + $msg)
    Write-Host 'ECS-SERVICE-CREDS-FAILED'
    exit 1
}
function Mask([string]$s) {
    if ([string]::IsNullOrEmpty($s)) { return '<empty>' }
    if ($s.Length -le 5) { return '****' }
    return $s.Substring(0, 3) + '****' + $s.Substring($s.Length - 2)
}

Write-Host '== 0. 前置检查 =='
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Host ('  管理员会话: ' + $isAdmin)
if (-not $isAdmin) { Fail '需以管理员身份运行(重启服务 + 写 C:\backend\.env); fail-closed 退出, 未写入任何内容' }
Write-Host ('  PowerShell: ' + $PSVersionTable.PSVersion.ToString())
Write-Host ('  目标 env 文件: ' + $EnvFile + '   存在: ' + (Test-Path $EnvFile))
if (-not (Test-Path $EnvFile)) { Fail '目标 .env 不存在; 请核对 backend 代码根(实测为 C:\backend), fail-closed 退出' }

$svc = Get-CimInstance Win32_Service -Filter ("Name='" + $ServiceName + "'") -EA SilentlyContinue
if (-not $svc) { Fail ('服务 ' + $ServiceName + ' 不存在; fail-closed 退出') }
Write-Host ('  服务 ' + $ServiceName + ' 状态: ' + $svc.State + '   启动模式: ' + $svc.StartMode + '   宿主: ' + (Split-Path $svc.PathName -Leaf))

# 确认 --env-file 确实指向本文件, 防止改错文件。
# nssm 形态陷阱(2026-11-09 实测): Win32_Service.ProcessId 指向 nssm.exe 本身, 它的命令行【不含】
# uvicorn 参数; 真正的启动参数在 nssm 注册表 Parameters\AppParameters 与其派生的 python 子进程里
# ⇒ 两处都查, 命中任一即放行。只回显"证据来源 + pid", 不打印完整命令行(依据 一.10 与 二.1)。
$envFileLc = $EnvFile.ToLower()
$evidence = @()
$nssmArgs = (Get-ItemProperty -Path ('HKLM:\SYSTEM\CurrentControlSet\Services\' + $ServiceName + '\Parameters') -Name AppParameters -EA SilentlyContinue).AppParameters
if ($nssmArgs -and $nssmArgs.ToLower().Contains($envFileLc)) { $evidence += 'nssm:AppParameters' }
# 排除 $PID 自身: 若操作员显式传 -EnvFile C:\backend\.env, 本脚本进程的命令行也会含该串(假阳性)
$procHits = @(Get-CimInstance Win32_Process -EA SilentlyContinue | Where-Object {
        $_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine.ToLower().Contains($envFileLc) })
if ($procHits.Count -gt 0) { $evidence += ('process:' + (($procHits | ForEach-Object { $_.ProcessId }) -join ',')) }
Write-Host ('  --env-file 指向本文件的证据: ' + $(if ($evidence.Count -gt 0) { $evidence -join ' | ' } else { 'NONE' }))
if ($evidence.Count -eq 0) { Fail '未发现任何配置或进程引用该 .env; 注入不会生效, fail-closed 退出, 未写入任何内容' }
Remove-Variable svc, nssmArgs, procHits, envFileLc -Force -EA SilentlyContinue

$backendDir = Split-Path $EnvFile -Parent
Write-Host ('  prod_creds.py 就位: ' + (Test-Path (Join-Path $backendDir 'prod_creds.py')))
Write-Host ('  e2e_auth_helper.py 就位: ' + (Test-Path (Join-Path $backendDir 'e2e_auth_helper.py')))
Write-Host '  注: 若 prod_creds.py 尚未部署, 线上 e2e_auth_helper 仍是硬编码常量版(不会 fail-closed);'
Write-Host '      本次注入是为部署 b83ad7ae 之后的版本做准备, 对现有运行无功能影响。'

Write-Host '== 1. 采集凭据(交互输入, 不落 shell 历史) =='
$phone = (Read-Host '  站点管理员手机号 (PROD_ADMIN_PHONE, 须纯数字)').Trim()
if ($phone -notmatch '^\d+$') { Fail ('管理员手机号字符集非法(须纯数字, 契约见 backend\.env.example 第七节): ' + (Mask $phone)) }

$s1 = Read-Host '  登录口令 (输入不回显)' -AsSecureString
$s2 = Read-Host '  再输一次确认' -AsSecureString
$b1 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s1)
$b2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s2)
try {
    $p1 = [Runtime.InteropServices.Marshal]::PtrToStringAuto($b1)
    $p2 = [Runtime.InteropServices.Marshal]::PtrToStringAuto($b2)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b1)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b2)
    Remove-Variable s1, s2 -Force -EA SilentlyContinue
}
if ($p1 -ne $p2) { Remove-Variable p1, p2 -Force -EA SilentlyContinue; Fail '两次口令不一致; fail-closed 退出, 未写入任何内容' }
# 字符集收紧是刻意设计: .env 里含 $ 反引号 分号 的口令既是 dotenv 解析歧义面, 也是远端命令拼接注入面
if ($p1 -notmatch '^[A-Za-z0-9]+$') {
    Remove-Variable p1, p2 -Force -EA SilentlyContinue
    Fail '口令字符集非法(须仅字母数字, 契约见 backend\.env.example:372)'
}
$pwdLen = $p1.Length
Remove-Variable p2 -Force -EA SilentlyContinue
Write-Host ('  管理员号: ' + (Mask $phone) + '   口令长度: ' + $pwdLen)

Write-Host '== 2. 备份 .env(依据 二.4 备份先于写入) =='
$bak = $EnvFile + '.bak-creds.' + (Get-Date -Format 'yyyyMMddHHmmss')
Copy-Item $EnvFile $bak -Force
Write-Host ('  已备份: ' + $bak + '   字节数: ' + (Get-Item $bak).Length)

Write-Host '== 3. 写入两键(就地替换优先, 行尾统一 LF) =='
$raw = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($EnvFile))
if ($raw.Length -gt 0 -and $raw[0] -eq [char]0xFEFF) {
    $raw = $raw.Substring(1)
    Write-Host '  原文件带 BOM, 写出时剥离(否则 dotenv 首键名会带上 U+FEFF)'
}
# 值行刻意用 LF 收尾: 原文件是 CRLF, 若值尾残留 \r 会被当成口令的一部分(uvicorn/dotenv 各版本
# 对 \r 的处理不一致), 导致登录静默失败。既有行的 CRLF 原样保留, 不做全文换行改造。
# 用 [ordered] 字典承载两键, 而不是 @(@('k','v'),...) —— PS 的 @() 会把嵌套数组【摊平】,
# 必须写 @(,@(...)) 才能保住层级; 那种形态太脆, 换成有序字典后语义无歧义且顺序确定。
$pair = [ordered]@{}
$pair['PROD_ADMIN_PHONE'] = $phone
$pair['PROD_ADMIN_PASSWORD'] = $p1
foreach ($k in $pair.Keys) {
    # Regex.Replace 的替换串里 $ 是特殊字符($1/$&), 故转义为 $$; 口令字符集已禁 $, 此处为纵深防御
    $v = ([string]$pair[$k]).Replace('$', '$$')
    $pat = '(?m)^[ \t]*' + $k + '[ \t]*=.*$'
    if ($raw -match $pat) {
        $raw = [regex]::Replace($raw, $pat, ($k + '=' + $v))
        Write-Host ('  就地替换: ' + $k)
    } else {
        if ($raw.Length -gt 0 -and -not $raw.EndsWith("`n")) { $raw += "`n" }
        $raw += "`n# ---- 生产登录凭据(t4-B 注入; 依据 .trae/rules/security-hard-coded.md 一.2 禁止硬编码) ----`n"
        $raw += $k + '=' + $v + "`n"
        Write-Host ('  追加新键: ' + $k)
    }
}
[IO.File]::WriteAllText($EnvFile, $raw, (New-Object Text.UTF8Encoding $false))
Remove-Variable raw, pair, k, v -Force -EA SilentlyContinue
Write-Host ('  已写回: ' + $EnvFile + '   字节数: ' + (Get-Item $EnvFile).Length)

Write-Host '== 4. 重启服务使 env 生效 =='
if ($NoRestart) {
    Write-Host ('  -NoRestart: 已跳过。env 未生效, 需手工执行 Restart-Service ' + $ServiceName + ' -Force')
} else {
    Write-Host ('  重启 ' + $ServiceName + ' 会中断在途请求(数秒)')
    $ans = (Read-Host '  现在重启? (y/N)').Trim().ToLower()
    if ($ans -ne 'y') {
        Write-Host ('  已跳过重启。env 未生效, 需手工执行 Restart-Service ' + $ServiceName + ' -Force')
    } else {
        try {
            Restart-Service $ServiceName -Force -EA Stop
            Start-Sleep -Seconds 5
            Write-Host ('  服务状态: ' + (Get-Service $ServiceName).Status)
        } catch {
            Fail ('重启失败: ' + $_.Exception.Message + ' —— .env 已写入, 可用步骤 2 的备份回滚')
        }
    }
}

Write-Host '== 5. 自检(只回显计数/长度/布尔, 绝不回显值) =='
$lines = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($EnvFile)) -split "`n"
foreach ($k in @('PROD_ADMIN_PHONE', 'PROD_ADMIN_PASSWORD')) {
    $hit = @($lines | Where-Object { $_ -match ('^[ \t]*' + $k + '[ \t]*=') })
    $hasCR = ($hit.Count -gt 0 -and $hit[0].Contains("`r"))
    $val = if ($hit.Count -gt 0) { ($hit[0] -replace '^[^=]*=', '') -replace "`r", '' } else { '' }
    $extra = ''
    if ($k -eq 'PROD_ADMIN_PASSWORD') { $extra = '   长度与采集时一致: ' + ($val.Length -eq $pwdLen) }
    elseif ($k -eq 'PROD_ADMIN_PHONE') { $extra = '   与采集时一致: ' + ($val -eq $phone) }
    Write-Host ('  ' + $k + '  行数(应为1)=' + $hit.Count + '  值非空=' + [bool]$val + '  值含CR(必须False)=' + $hasCR + $extra)
    Remove-Variable val -Force -EA SilentlyContinue
    if ($hit.Count -ne 1) { Fail ($k + ' 命中 ' + $hit.Count + ' 行(应为 1); 重复键会让 dotenv 取值歧义') }
    if ($hasCR) { Fail ($k + ' 值尾残留 CR, 口令会被解析成带 \r 的字符串导致登录失败') }
}
Remove-Variable lines, pwdLen -Force -EA SilentlyContinue

$ln = @(Get-NetTCPConnection -LocalPort $Port -State Listen -EA SilentlyContinue)
Write-Host ('  端口 ' + $Port + ' 监听: ' + ($ln.Count -gt 0) + '   pid=' + $(if ($ln.Count -gt 0) { $ln[0].OwningProcess } else { '-' }))

# loopback 真实登录: 唯一能证明"注入的凭据确实可用"的判据。失败只告警不 Fail——
# 线上可能仍是硬编码常量版(不读 env), 或该号未开通 admin 角色, 均不代表注入本身失败。
try {
    $body = ([pscustomobject]@{ phone = $phone; password = $p1 }) | ConvertTo-Json -Compress
    $r = Invoke-RestMethod -Uri ('http://127.0.0.1:' + $Port + '/api/auth/login') -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 20
    Write-Host ('  loopback 登录: 成功   返回 accessToken: ' + [bool]$r.accessToken)
} catch {
    Write-Host ('  loopback 登录: 未通过(建议性检查, 不否定注入结果) —— ' + $_.Exception.Message)
}
Remove-Variable body, r, p1 -Force -EA SilentlyContinue

Write-Host '== 6. 回滚与后续 =='
Write-Host ('  回滚: Copy-Item ' + $bak + ' ' + $EnvFile + ' -Force ; Restart-Service ' + $ServiceName + ' -Force')
Write-Host '  后续: 部署 b83ad7ae 的 backend 代码(e2e_auth_helper.py + prod_creds.py)到 C:\backend 后,'
Write-Host '        再重启一次服务; 届时 fail-closed 版会读这两个键, 缺失即 throw(不发)。'
Write-Host 'ECS-SERVICE-CREDS-DONE'
