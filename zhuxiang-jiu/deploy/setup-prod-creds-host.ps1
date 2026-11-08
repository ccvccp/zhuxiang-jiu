# t4-A 生产登录凭据注入(ECS 主机侧) —— 交互式, 必须在 ECS 的 RDP 会话里以【管理员】身份本地运行
# 用法(ECS 上): powershell -ExecutionPolicy Bypass -File D:\zhuxiang\zhuxiang-jiu\deploy\setup-prod-creds-host.ps1
# 【不可走云助手 RunCommand】本脚本含 Read-Host 交互; 且口令不得经云助手命令/OSS 中转/任何日志传输
#   (依据 .trae/rules/security-hard-coded.md 一.2 + 用户裁定"口令不下发, 我自己在 ECS 上设")
# 产物: 1) 仓外 JSON 凭据文件(ACL 收紧, 无 BOM)  2) 机器级环境变量 ZX_CRED_FILE / PROD_ADMIN_PHONE / ZX_API_PHONE / E2E_MEMBER_PHONE
#   —— 注册表里只存"路径"与"号码", 口令绝不进注册表:
#      setx /M 会让口令落 HKLM\...\Session Manager\Environment(同机任意管理员可读) + 1024 字符截断 + 进 shell 历史
# 幂等: 可重复运行; 已存在的 JSON 覆盖前先备份(依据 二.4 备份先于写入)
# 下一步: 跑完本脚本再跑 setup-prod-creds-container.ps1(B 段, 容器侧)
param(
    [string]$CredDir = 'D:\zhuxiang-secrets',
    [string]$CredFileName = 'sv73_cred.json'
)
$ErrorActionPreference = 'Continue'

function Fail([string]$msg) {
    Write-Host ('  [FAIL] ' + $msg)
    Write-Host 'HOST-CREDS-FAILED'
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
if (-not $isAdmin) { Fail '需以管理员身份运行(写机器级环境变量 HKLM 必须管理员); fail-closed 退出, 未写入任何内容' }
Write-Host ('  PowerShell: ' + $PSVersionTable.PSVersion.ToString())
Write-Host ('  凭据目录: ' + $CredDir)

Write-Host '== 1. 采集凭据(交互输入, 不落 shell 历史) =='
$phone = (Read-Host '  站点管理员手机号 (PROD_ADMIN_PHONE, 须纯数字)').Trim()
if ($phone -notmatch '^\d+$') { Fail ('管理员手机号字符集非法(须纯数字, 契约见 backend\.env.example 第七节): ' + (Mask $phone)) }

$member = (Read-Host '  会员手机号 (E2E_MEMBER_PHONE, 须纯数字; 直接回车=与管理员同号)').Trim()
if ($member -eq '') { $member = $phone }
if ($member -notmatch '^\d+$') { Fail ('会员手机号字符集非法(须纯数字): ' + (Mask $member)) }

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
if ($p1 -notmatch '^[A-Za-z0-9]+$') {
    Remove-Variable p1, p2 -Force -EA SilentlyContinue
    Fail '口令字符集非法(须仅字母数字) —— 含 $ 反引号 分号 等符号在远端 pipe 拼接里就是注入面(契约见 backend\.env.example:372)'
}
$pwdLen = $p1.Length
Write-Host ('  管理员号: ' + (Mask $phone) + '   会员号: ' + (Mask $member) + '   口令长度: ' + $pwdLen)

Write-Host '== 2. 写仓外 JSON(无 BOM + ACL 收紧) =='
if (-not (Test-Path $CredDir)) { New-Item -ItemType Directory -Path $CredDir -Force | Out-Null }
$jsonPath = Join-Path $CredDir $CredFileName
if (Test-Path $jsonPath) {
    $bak = $jsonPath + '.bak.' + (Get-Date -Format 'yyyyMMddHHmmss')
    Copy-Item $jsonPath $bak -Force
    Write-Host ('  已备份旧文件: ' + (Split-Path $bak -Leaf))
}
# 字段名逐字对齐 5 个消费方(Get-Sv73Cred)与 Python 侧 prod_creds._CRED_FILE_FIELDS: 只有 phone / password
# 刻意不加多余键, 保持与 sv73_publish_process.ps1:26 等处注释『形如 {"phone":"","password":""}』一致
$credJson = ([pscustomobject]@{ phone = $phone; password = $p1 }) | ConvertTo-Json -Compress
# 关键: 凭据 JSON 必须【无 BOM】—— PS 侧读法是 [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes(...)),
#       它【不剥 BOM】; 带 BOM 会让首字符变 U+FEFF, PS5.1 ConvertFrom-Json 直接解析失败。
#       (与 .ps1 脚本文件"必须带 BOM"的规则方向【相反】, 两者不可混用)
[IO.File]::WriteAllText($jsonPath, $credJson, (New-Object Text.UTF8Encoding $false))
Remove-Variable p1, p2, credJson -Force -EA SilentlyContinue
Write-Host ('  已写入: ' + $jsonPath)

$me = "$env:USERDOMAIN\$env:USERNAME"
icacls $jsonPath /inheritance:r /grant:r "${me}:R" 'SYSTEM:R' 'Administrators:R' | Out-Null
Write-Host ('  ACL 条数: ' + (@(icacls $jsonPath).Count) + '  (已剥离继承, 仅 ' + $me + ' / SYSTEM / Administrators 只读)')

Write-Host '== 3. 注册机器级环境变量(只存路径与号码, 不存口令) =='
$envMap = [ordered]@{
    'ZX_CRED_FILE'     = $jsonPath
    'PROD_ADMIN_PHONE' = $phone
    'ZX_API_PHONE'     = $phone
    'E2E_MEMBER_PHONE' = $member
}
foreach ($k in $envMap.Keys) {
    [Environment]::SetEnvironmentVariable($k, $envMap[$k], 'Machine')
    Set-Item -Path ('Env:' + $k) -Value $envMap[$k]
    if ($k -eq 'ZX_CRED_FILE') { Write-Host ('  [Machine] ' + $k + ' = ' + $envMap[$k]) }
    else { Write-Host ('  [Machine] ' + $k + ' = ' + (Mask $envMap[$k])) }
}
Write-Host '  注: PROD_ADMIN_PASSWORD / ZX_API_PASSWORD 刻意【不】登记注册表, 只经 JSON 文件与容器 .env 承载'

Write-Host '== 4. 让计划任务新实例继承机器级 env =='
# 机器级 env 变更对【已运行进程】不可见; 计划任务服务需重启才会用新环境块派生子进程
try {
    Restart-Service Schedule -Force -EA Stop
    Start-Sleep -Seconds 3
    Write-Host ('  Schedule 服务状态: ' + (Get-Service Schedule).Status)
} catch {
    Write-Host ('  [警告] Restart-Service Schedule 失败: ' + $_.Exception.Message)
    Write-Host '         计划任务派生的新进程可能仍用旧环境块 —— 需在发布窗口前重启 ECS 一次'
}
foreach ($tn in @('zx-sv73-publish-process', 'zx-sv73-autopublish', 'zxjiu-stats-monitor')) {
    $t = Get-ScheduledTask -TaskName $tn -EA SilentlyContinue
    if ($t) {
        $i = $t | Get-ScheduledTaskInfo
        Write-Host ('  ' + $tn + '  状态:' + $t.State + '  上次:' + $i.LastRunTime + '  结果:' + $i.LastTaskResult + '  下次:' + $i.NextRunTime)
    } else {
        Write-Host ('  ' + $tn + ' : 本机不存在该任务(跳过)')
    }
}

Write-Host '== 5. 主机侧验证(只回显长度/布尔, 不回显值) =='
$bytes = [IO.File]::ReadAllBytes($jsonPath)
$hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
Write-Host ('  JSON 字节数: ' + $bytes.Length + '   含BOM(必须 False): ' + $hasBom)
if ($hasBom) { Fail 'JSON 带 BOM, Get-Sv73Cred 的 ConvertFrom-Json 会解析失败' }
$o = ([Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json)
Write-Host ('  phone 非空: ' + [bool]$o.phone + '   password 非空: ' + [bool]$o.password)
Write-Host ('  phone 纯数字: ' + ($o.phone -match '^\d+$') + '   password 字母数字: ' + ($o.password -match '^[A-Za-z0-9]+$'))
Write-Host ('  password 长度与采集时一致: ' + ($o.password.Length -eq $pwdLen))
Remove-Variable o, bytes -Force -EA SilentlyContinue

$regPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment'
foreach ($k in @('ZX_CRED_FILE', 'PROD_ADMIN_PHONE', 'ZX_API_PHONE', 'E2E_MEMBER_PHONE')) {
    $v = (Get-ItemProperty -Path $regPath -Name $k -EA SilentlyContinue).$k
    Write-Host ('  [Registry] ' + $k + ' 已登记: ' + [bool]$v)
}
$mp = [Environment]::GetEnvironmentVariable('ZX_CRED_FILE', 'Machine')
Write-Host ('  [Machine] ZX_CRED_FILE 新进程可读: ' + [bool]$mp + '   指向文件存在: ' + (Test-Path $mp))

Write-Host '== 6. 下一步(B 段 · 容器侧) =='
Write-Host '  A 段完成。接着在 ECS 上运行:'
Write-Host '    powershell -ExecutionPolicy Bypass -File D:\zhuxiang\zhuxiang-jiu\deploy\setup-prod-creds-container.ps1'
Write-Host '  若 ECS 代码已同步到 b83ad7ae 但镜像仍是旧的(容器内无 prod_creds.py), 加 -Build:'
Write-Host '    powershell -ExecutionPolicy Bypass -File D:\zhuxiang\zhuxiang-jiu\deploy\setup-prod-creds-container.ps1 -Build'
Write-Host 'HOST-CREDS-DONE'
