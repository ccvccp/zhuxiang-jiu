# t12 生产站点管理员口令轮换 —— 走线上改密接口, 不碰数据库
# 用法(二选一):
#   ECS 本地: powershell -ExecutionPolicy Bypass -File C:\release\rotate-prod-password.ps1
#   云助手:   powershell -File deploy\aliyun-ecs-rpc.ps1 Send -CommandFile deploy\rotate-prod-password.ps1 `
#               -CommandName rotate-pwd -RegionId cn-beijing -InstanceId i-2zebz5dnx9903xdsbi7m
#   能走云助手的前提: 本脚本【不含任何口令字面量】, 旧口令在 ECS 本地从 .env / 凭据 JSON 读出,
#   新口令在 ECS 本地用 CSPRNG 生成并只写回 ECS 本地落点; 口令值全程不出机、不进 stdout
#   (依据 .trae/rules/security-hard-coded.md 一.2 + 二.1)。加了 -Manual(交互输入)则【只能】RDP 里跑。
#
# 为什么不是改库: 会员存储是 Redis(键 zhuxiang:member:{id} 的 password 字段, 手机号索引
#   zhuxiang:member:phone:{phone}), 口令是 PBKDF2-SHA256 / 120000 迭代 + 每用户随机盐
#   (core\auth.py:65-89 hash_password / verify_password), 没有 SQL 可改, 自己算哈希直写 Redis 风险更高。
#   线上已有现成改密通道 POST /api/auth/change-password(routes\auth_routes.py:475-491 →
#   services\auth_service.py:921-969): 校验旧口令 → 写新哈希 → 级联吊销该号【全部】旧令牌 → 返回新令牌对。
#   ⚠️ 别误用 /api/admin/users/{id}/reset-password(admin_routes.py:351), 那是后台 admin 用户体系, 不含会员。
#
# 产物: 1) 线上该账号口令已变  2) C:\backend\.env 的 PROD_ADMIN_PASSWORD 同步(ZX_API_PASSWORD 仅当已存在时就地改)
#       3) ZX_CRED_FILE 指向的凭据 JSON 的 password 字段同步  4) 重启 backend8147 使 env 生效
# 幂等: 【不可】重复运行 —— 第二次旧口令已失效, 会在第 4 步 fail-closed 退出, 线上与落点都不动。
# 补偿: 第 6 步写落点失败时, 本脚本自动再调一次改密接口把口令改回旧值, 保证"线上 ↔ 落点"不脱节。
#
# ⚠️ 轮换后【绝不要】跑 scripts\seed_redis.py --force —— 它会把口令刷回种子值
#    (scripts\seed_redis.py:211-228, 口令硬编码在 217 行), 等于把本次轮换作废。
#    正常 Restart-Service backend8147 【不会】重新种子(uvicorn 不执行 seed_redis), 所以第 7 步重启是安全的。
# 依据: 一.2(禁硬编码/禁入日志) 一.7(禁 Get-Random 生成安全随机数 → 用 RandomNumberGenerator)
#       二.1(日志脱敏) 二.3(fail-closed) 二.4(备份先于写入)
param(
    [string]$EnvFile = 'C:\backend\.env',
    [string]$ServiceName = 'backend8147',
    [string]$BaseUrl = 'http://127.0.0.1:8000',
    [string]$Phone = '',
    [int]$NewPasswordLength = 20,
    [switch]$Manual,
    [switch]$NoRestart
)
$ErrorActionPreference = 'Continue'

function Fail([string]$msg) {
    Write-Host ('  [FAIL] ' + $msg)
    Write-Host 'ROTATE-CREDS-FAILED'
    exit 1
}
function Mask([string]$s) {
    if ([string]::IsNullOrEmpty($s)) { return '<empty>' }
    if ($s.Length -le 5) { return '****' }
    return $s.Substring(0, 3) + '****' + $s.Substring($s.Length - 2)
}
# 从 dotenv 原文里取一个键的值(剥引号与 CR); 取不到返回空串
function Read-EnvKey([string]$raw, [string]$key) {
    $m = [regex]::Match($raw, ('(?m)^[ \t]*' + [regex]::Escape($key) + '[ \t]*=(.*)$'))
    if (-not $m.Success) { return '' }
    return ($m.Groups[1].Value).Trim().Trim('"').Trim("'").Trim()
}
# CSPRNG 生成纯字母数字口令。一.7 明令禁止 Get-Random(非密码学安全);
# 用拒绝采样消除模偏差: 字母表 62 → 上界 floor(256/62)*62 = 248, 只接受 b < 248。
function New-SecureAlnum([int]$len) {
    $alpha = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    $max = [byte]([Math]::Floor(256 / $alpha.Length) * $alpha.Length)
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $sb = New-Object Text.StringBuilder
    $buf = New-Object 'byte[]' 64
    try {
        while ($sb.Length -lt $len) {
            $rng.GetBytes($buf)
            foreach ($b in $buf) {
                if ($sb.Length -ge $len) { break }
                if ($b -lt $max) { [void]$sb.Append($alpha[$b % $alpha.Length]) }
            }
        }
        return $sb.ToString()
    } finally {
        $rng.Dispose()
        [Array]::Clear($buf, 0, $buf.Length)
    }
}

Write-Host '== 0. 前置检查 =='
Write-Host ('  PowerShell: ' + $PSVersionTable.PSVersion.ToString())
Write-Host ('  后端基址  : ' + $BaseUrl)
Write-Host ('  env 文件  : ' + $EnvFile + '   存在: ' + (Test-Path $EnvFile))
if (-not (Test-Path $EnvFile)) { Fail ('目标 .env 不存在(实测 backend 代码根为 C:\backend); fail-closed 退出, 未做任何变更') }
if ($NewPasswordLength -lt 12 -or $NewPasswordLength -gt 64) {
    Fail ('新口令长度 ' + $NewPasswordLength + ' 越界(本脚本限 12-64: 接口侧上限 64, 下限 12 是熵要求); 未做任何变更')
}
$svc = Get-CimInstance Win32_Service -Filter ("Name='" + $ServiceName + "'") -EA SilentlyContinue
Write-Host ('  服务 ' + $ServiceName + ': ' + $(if ($svc) { $svc.State + ' / ' + $svc.StartMode } else { 'ABSENT' }))
Remove-Variable svc -Force -EA SilentlyContinue

$rawEnv = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($EnvFile))
if ($rawEnv.Length -gt 0 -and $rawEnv[0] -eq [char]0xFEFF) { $rawEnv = $rawEnv.Substring(1) }

# 凭据 JSON(A 段 setup-prod-creds-host.ps1 的产物); 注册表里只存路径, 不存口令
$credPath = [Environment]::GetEnvironmentVariable('ZX_CRED_FILE', 'Machine')
if (-not $credPath) { $credPath = $env:ZX_CRED_FILE }
$credObj = $null
if ($credPath -and (Test-Path $credPath)) {
    # 逐字复刻既有 5 个消费方的读法: ReadAllBytes + UTF8.GetString(不剥 BOM, 该文件本就无 BOM)
    $credObj = ([Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($credPath)) | ConvertFrom-Json)
    Write-Host ('  凭据 JSON : ' + $credPath + '   字段: ' + (($credObj.PSObject.Properties.Name) -join ','))
} else {
    Write-Host ('  凭据 JSON : 未登记或不存在(ZX_CRED_FILE=' + $(if ($credPath) { $credPath } else { '<empty>' }) + ') —— 本次只回写 .env')
}

Write-Host '== 1. 取旧口令(只从 ECS 本地落点读, 不回显值) =='
$phoneSrc = ''; $oldP = ''; $oldSrc = ''
if ($credObj -and [string]$credObj.password) { $oldP = [string]$credObj.password; $oldSrc = 'ZX_CRED_FILE JSON' }
if (-not $oldP) {
    $v = Read-EnvKey $rawEnv 'PROD_ADMIN_PASSWORD'
    if ($v) { $oldP = $v; $oldSrc = $EnvFile + ':PROD_ADMIN_PASSWORD' }
    Remove-Variable v -Force -EA SilentlyContinue
}
if (-not $oldP) {
    $v = Read-EnvKey $rawEnv 'ZX_API_PASSWORD'
    if ($v) { $oldP = $v; $oldSrc = $EnvFile + ':ZX_API_PASSWORD' }
    Remove-Variable v -Force -EA SilentlyContinue
}
if (-not $oldP -and $Manual) {
    $s1 = Read-Host '  本地落点读不到旧口令, 请手工输入(不回显)' -AsSecureString
    $b1 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s1)
    try { $oldP = [Runtime.InteropServices.Marshal]::PtrToStringAuto($b1) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b1); Remove-Variable s1 -Force -EA SilentlyContinue }
    $oldSrc = 'interactive'
}
if (-not $oldP) {
    Fail '本地落点(.env / ZX_CRED_FILE JSON)都读不到旧口令; 请重跑并加 -Manual 交互输入。fail-closed 退出, 未做任何变更'
}
if ($oldP -notmatch '^[A-Za-z0-9]+$') {
    Write-Host '  [警告] 落点里的旧口令含非字母数字字符(可能是 CRLF 污染或 dotenv 引号残留); 先修 .env 再轮换'
    Remove-Variable oldP -Force -EA SilentlyContinue
    Fail ('旧口令字符集非法, 长度=' + $oldSrc + ' 处读出; fail-closed 退出, 未做任何变更')
}

if ($Phone) { $phone = $Phone.Trim(); $phoneSrc = '-Phone 参数' }
if (-not $phone) { $v = Read-EnvKey $rawEnv 'PROD_ADMIN_PHONE'; if ($v) { $phone = $v; $phoneSrc = $EnvFile + ':PROD_ADMIN_PHONE' }; Remove-Variable v -Force -EA SilentlyContinue }
if (-not $phone -and $credObj -and [string]$credObj.phone) { $phone = [string]$credObj.phone; $phoneSrc = 'ZX_CRED_FILE JSON' }
if (-not $phone) { $v = [Environment]::GetEnvironmentVariable('PROD_ADMIN_PHONE', 'Machine'); if ($v) { $phone = $v; $phoneSrc = 'Machine env' }; Remove-Variable v -Force -EA SilentlyContinue }
if ($phone -notmatch '^\d+$') {
    Remove-Variable oldP -Force -EA SilentlyContinue
    Fail ('手机号缺失或非法(须纯数字, 来源=' + $phoneSrc + '); 请重跑并加 -Phone。fail-closed 退出, 未做任何变更')
}
$oldLen = $oldP.Length
Write-Host ('  账号    : ' + (Mask $phone) + '   (来源: ' + $phoneSrc + ')')
Write-Host ('  旧口令  : 长度 ' + $oldLen + '   掩码 ' + (Mask $oldP) + '   (来源: ' + $oldSrc + ')')

Write-Host '== 2. 生成新口令(CSPRNG, 不回显值) =='
if ($Manual) {
    $s1 = Read-Host '  新口令(须仅字母数字, 12-64 位, 不回显)' -AsSecureString
    $s2 = Read-Host '  再输一次确认' -AsSecureString
    $b1 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s1)
    $b2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s2)
    try {
        $n1 = [Runtime.InteropServices.Marshal]::PtrToStringAuto($b1)
        $n2 = [Runtime.InteropServices.Marshal]::PtrToStringAuto($b2)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b1); [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b2)
        Remove-Variable s1, s2 -Force -EA SilentlyContinue
    }
    if ($n1 -ne $n2) { Remove-Variable n1, n2, oldP -Force -EA SilentlyContinue; Fail '两次新口令不一致; fail-closed 退出, 未做任何变更' }
    $newP = $n1
    Remove-Variable n1, n2 -Force -EA SilentlyContinue
    if ($newP -notmatch '^[A-Za-z0-9]+$' -or $newP.Length -lt 12 -or $newP.Length -gt 64) {
        Remove-Variable newP, oldP -Force -EA SilentlyContinue
        Fail '新口令须为 12-64 位纯字母数字(契约见 backend\.env.example:372: 符号在远端 pipe 拼接里是注入面); 未做任何变更'
    }
} else {
    $newP = New-SecureAlnum $NewPasswordLength
    # 接口要求新旧不得相同; 20 位随机碰撞概率可忽略, 这里只是把契约显式钉死
    $guard = 0
    while ($newP -eq $oldP -and $guard -lt 5) { $newP = New-SecureAlnum $NewPasswordLength; $guard++ }
    if ($newP -eq $oldP) { Remove-Variable newP, oldP -Force -EA SilentlyContinue; Fail '生成的新口令与旧口令相同(极不可能, 疑似 CSPRNG 异常); 未做任何变更' }
}
if ($newP -eq $oldP) { Remove-Variable newP, oldP -Force -EA SilentlyContinue; Fail '新旧口令相同, 接口必拒; 未做任何变更' }
$newLen = $newP.Length
Write-Host ('  新口令  : 长度 ' + $newLen + '   纯字母数字 True   与旧口令不同 True   来源 ' + $(if ($Manual) { 'interactive' } else { 'RandomNumberGenerator(拒绝采样)' }))
Write-Host '  注: 新口令刻意【不】打印到 stdout —— 云助手 RunCommand 的输出会进 DescribeInvocationResults 留存(二.1)。'
Write-Host '      轮换后它只存在于 ECS 的 .env 与凭据 JSON 里, 人工登录请在 ECS 上以管理员身份读取。'

Write-Host '== 3. 备份落点(依据 二.4 备份先于写入) =='
$stamp = Get-Date -Format 'yyyyMMddHHmmss'
$bakEnv = $EnvFile + '.bak-rotate.' + $stamp
Copy-Item $EnvFile $bakEnv -Force
Write-Host ('  已备份: ' + $bakEnv + '   字节数: ' + (Get-Item $bakEnv).Length)
$bakJson = ''
if ($credObj -and $credPath) {
    $bakJson = $credPath + '.bak.' + $stamp
    Copy-Item $credPath $bakJson -Force
    Write-Host ('  已备份: ' + $bakJson + '   字节数: ' + (Get-Item $bakJson).Length)
}

Write-Host '== 4. 用旧口令登录取令牌 =='
$loginBody = ([pscustomobject]@{ phone = $phone; password = $oldP }) | ConvertTo-Json -Compress
try {
    $r1 = Invoke-RestMethod -Uri ($BaseUrl + '/api/auth/login') -Method Post -ContentType 'application/json' -Body $loginBody -TimeoutSec 30
    $tok = [string]$r1.accessToken
} catch {
    $code = 0
    if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
    Remove-Variable loginBody, oldP, newP -Force -EA SilentlyContinue
    Fail ('旧口令登录失败(HTTP ' + $code + ') —— 落点里的旧口令已不是线上真值, 或号码不对。fail-closed 退出, 线上口令【未】变更, 落点【未】写入')
}
Remove-Variable loginBody -Force -EA SilentlyContinue
if (-not $tok) { Remove-Variable oldP, newP -Force -EA SilentlyContinue; Fail '登录返回成功但无 accessToken 字段; fail-closed 退出, 线上口令未变更' }
Write-Host ('  登录: 成功   accessToken 长度 ' + $tok.Length + '   refreshToken ' + [bool]$r1.refreshToken)

Write-Host '== 5. 调用改密接口(线上口令在此刻真正变更) =='
$cpBody = ([pscustomobject]@{ oldPassword = $oldP; newPassword = $newP }) | ConvertTo-Json -Compress
try {
    $r2 = Invoke-RestMethod -Uri ($BaseUrl + '/api/auth/change-password') -Method Post -ContentType 'application/json' -Headers @{ Authorization = ('Bearer ' + $tok) } -Body $cpBody -TimeoutSec 30
    $cpCode = 200; $cpErr = ''
} catch {
    $cpCode = 0
    if ($_.Exception.Response) { $cpCode = [int]$_.Exception.Response.StatusCode }
    $cpErr = $_.Exception.Message
}
Remove-Variable cpBody -Force -EA SilentlyContinue
if ($cpCode -ne 200) {
    Remove-Variable oldP, newP -Force -EA SilentlyContinue
    Fail ('改密接口返回 HTTP ' + $cpCode + ' (' + $cpErr + ') —— 线上口令【未】变更, 落点【未】写入。常见原因: 新口令不在 6-64 位 / 与旧口令相同 / 令牌过期')
}
# 改密成功后旧令牌被级联吊销, 接口会返回新令牌对; 补偿改回时要用新令牌
$tok2 = [string]$r2.accessToken
if (-not $tok2) { $tok2 = $tok }
Write-Host ('  改密: 成功   返回新 accessToken ' + [bool]$r2.accessToken + '   该号全部旧令牌已吊销(任何长期 token 需重新登录)')
Remove-Variable r1, r2, tok -Force -EA SilentlyContinue

Write-Host '== 6. 回写落点(失败则补偿: 自动改回旧口令) =='
$writeErr = ''
try {
    # 6a. .env —— 就地替换优先, 值行统一 LF(值尾残留 \r 会被当成口令的一部分, 导致登录静默失败)
    $raw = $rawEnv
    $pair = [ordered]@{}
    $pair['PROD_ADMIN_PASSWORD'] = $newP
    # ZX_API_PASSWORD 是等价别名(backend\.env.example:379); 只在原本就有时才改, 不凭空新增键
    if ($raw -match '(?m)^[ \t]*ZX_API_PASSWORD[ \t]*=') { $pair['ZX_API_PASSWORD'] = $newP }
    foreach ($k in $pair.Keys) {
        # Regex.Replace 的替换串里 $ 是特殊字符($1/$&), 转义为 $$; 口令字符集已禁 $, 此处为纵深防御
        $v = ([string]$pair[$k]).Replace('$', '$$')
        $pat = '(?m)^[ \t]*' + $k + '[ \t]*=.*$'
        if ($raw -match $pat) { $raw = [regex]::Replace($raw, $pat, ($k + '=' + $v)); Write-Host ('  就地替换: ' + $k) }
        else {
            if ($raw.Length -gt 0 -and -not $raw.EndsWith("`n")) { $raw += "`n" }
            $raw += "`n# ---- t12 口令轮换(" + (Get-Date -Format 'yyyy-MM-dd HH:mm') + '; 依据 .trae/rules/security-hard-coded.md 一.2) ----`n'
            $raw += $k + '=' + $v + "`n"
            Write-Host ('  追加新键: ' + $k)
        }
    }
    [IO.File]::WriteAllText($EnvFile, $raw, (New-Object Text.UTF8Encoding $false))
    Write-Host ('  已写回: ' + $EnvFile + '   字节数: ' + (Get-Item $EnvFile).Length + '   改写键数: ' + $pair.Count)
    Remove-Variable raw, pair, k, v -Force -EA SilentlyContinue

    # 6b. 凭据 JSON —— 必须【无 BOM】: 消费方读法是 UTF8.GetString(ReadAllBytes(...)), 不剥 BOM
    if ($credObj -and $credPath) {
        $credObj.password = $newP
        $cj = ([pscustomobject]@{ phone = [string]$credObj.phone; password = [string]$credObj.password }) | ConvertTo-Json -Compress
        [IO.File]::WriteAllText($credPath, $cj, (New-Object Text.UTF8Encoding $false))
        Write-Host ('  已写回: ' + $credPath + '   字节数: ' + (Get-Item $credPath).Length + '   BOM(应False): ' + (((Get-Item $credPath).Length -ge 3) -and ([IO.File]::ReadAllBytes($credPath))[0] -eq 0xEF))
        Remove-Variable cj -Force -EA SilentlyContinue
    }
} catch {
    $writeErr = $_.Exception.Message
}

if ($writeErr) {
    Write-Host ('  [警告] 落点写入失败: ' + $writeErr)
    Write-Host '  补偿中: 用新令牌把线上口令改回旧值, 避免"线上=新 / 落点=旧"脱节 ...'
    $rbBody = ([pscustomobject]@{ oldPassword = $newP; newPassword = $oldP }) | ConvertTo-Json -Compress
    try {
        Invoke-RestMethod -Uri ($BaseUrl + '/api/auth/change-password') -Method Post -ContentType 'application/json' -Headers @{ Authorization = ('Bearer ' + $tok2) } -Body $rbBody -TimeoutSec 30 | Out-Null
        Write-Host '  补偿成功: 线上口令已还原为旧值, 线上与落点一致。修好写入问题(磁盘/权限/占用)后可直接重跑本脚本'
    } catch {
        # 双重失败 = 线上是新口令但落点是旧口令, 且新口令只存在于本进程内存 ⇒ 必须落地, 否则账号锁死
        $emg = $EnvFile + '.newpwd.' + $stamp
        [IO.File]::WriteAllText($emg, $newP, (New-Object Text.UTF8Encoding $false))
        $me = "$env:USERDOMAIN\$env:USERNAME"
        icacls $emg /inheritance:r /grant:r "${me}:R" 'SYSTEM:R' 'Administrators:R' | Out-Null
        Write-Host '  [FAIL] 补偿也失败 —— 处于不一致状态: 线上=新口令, 落点=旧口令'
        Write-Host ('         新口令已应急落盘(ACL 仅 ' + $me + ' / SYSTEM / Administrators 只读): ' + $emg)
        Write-Host ('         人工处置: 把该文件内容写入 ' + $EnvFile + ' 的 PROD_ADMIN_PASSWORD 与 ' + $(if ($credPath) { $credPath } else { '<凭据JSON>' }) + ',')
        Write-Host ('                   然后 Restart-Service ' + $ServiceName + ' -Force, 最后 DeleteFile 删掉该应急文件')
    }
    Remove-Variable rbBody, oldP, newP -Force -EA SilentlyContinue
    Fail '落点写入失败(已尝试补偿)'
}
Remove-Variable writeErr -Force -EA SilentlyContinue

Write-Host '== 7. 重启服务使 env 生效 =='
# 刻意【不】交互确认: 云助手 RunCommand 无 stdin, Read-Host 会拿到空串而静默跳过重启,
# 结果 .env 已是新口令但运行进程仍持旧口令 ⇒ prod_creds 读到错值。宁可默认重启也不留半生效状态。
# 重启安全性: uvicorn 启动不执行 seed_redis, 不会把口令刷回种子值(见文件头 ⚠️)。
if ($NoRestart) {
    Write-Host ('  -NoRestart: 已跳过。⚠️ 运行中的进程仍持旧口令 env, 需手工 Restart-Service ' + $ServiceName + ' -Force')
} else {
    Write-Host ('  重启 ' + $ServiceName + ' 会中断在途请求(数秒)')
    try {
        Restart-Service $ServiceName -Force -EA Stop
        Start-Sleep -Seconds 6
        Write-Host ('  服务状态: ' + (Get-Service $ServiceName).Status)
    } catch {
        Write-Host ('  [警告] 重启失败: ' + $_.Exception.Message + ' —— 口令已轮换且落点已写入, 但 env 未生效')
        Write-Host ('         手工执行: Restart-Service ' + $ServiceName + ' -Force   (回滚见第 9 步)')
    }
}
$ln = @(Get-NetTCPConnection -LocalPort ([uri]$BaseUrl).Port -State Listen -EA SilentlyContinue)
Write-Host ('  端口 ' + ([uri]$BaseUrl).Port + ' 监听: ' + ($ln.Count -gt 0) + '   pid=' + $(if ($ln.Count -gt 0) { $ln[0].OwningProcess } else { '-' }))
Remove-Variable ln -Force -EA SilentlyContinue

Write-Host '== 8. 验证(新口令可登 + 旧口令失效 + .env 自检) =='
$nb = ([pscustomobject]@{ phone = $phone; password = $newP }) | ConvertTo-Json -Compress
$newOk = $false; $newTok = $false
try { $rn = Invoke-RestMethod -Uri ($BaseUrl + '/api/auth/login') -Method Post -ContentType 'application/json' -Body $nb -TimeoutSec 30; $newOk = $true; $newTok = [bool]$rn.accessToken } catch { }
Remove-Variable nb, rn -Force -EA SilentlyContinue
Write-Host ('  新口令登录(应为 True): ' + $newOk + '   返回 accessToken: ' + $newTok)
if (-not $newOk) {
    Remove-Variable oldP, newP -Force -EA SilentlyContinue
    Fail ('新口令登录不通 —— 线上可能未真正改密成功。请用 ' + $bakEnv + ' 与 ' + $(if ($bakJson) { $bakJson } else { '<无JSON备份>' }) + ' 比对, 并按第 9 步回滚')
}

$ob = ([pscustomobject]@{ phone = $phone; password = $oldP }) | ConvertTo-Json -Compress
$oldStillWorks = $true
try { Invoke-RestMethod -Uri ($BaseUrl + '/api/auth/login') -Method Post -ContentType 'application/json' -Body $ob -TimeoutSec 30 | Out-Null } catch { $oldStillWorks = $false }
Remove-Variable ob, oldP -Force -EA SilentlyContinue
Write-Host ('  旧口令已失效(应为 True): ' + (-not $oldStillWorks) + '   ← 这条才是"git 历史里的泄漏口令已作废"的判据')
if ($oldStillWorks) { Write-Host '  [警告] 旧口令仍能登录 ⇒ 泄漏面【未】关闭, 本次轮换等于没做; 请检查是否打到了同一后端实例' }

$lines = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($EnvFile)) -split "`n"
foreach ($k in @('PROD_ADMIN_PASSWORD', 'ZX_API_PASSWORD')) {
    $hit = @($lines | Where-Object { $_ -match ('^[ \t]*' + $k + '[ \t]*=') })
    if ($hit.Count -eq 0) { Write-Host ('  ' + $k + ': 不存在(本次未新增, 正常)'); continue }
    $val = ($hit[0] -replace '^[^=]*=', '') -replace "`r", ''
    Write-Host ('  ' + $k + '  行数(应为1)=' + $hit.Count + '  值非空=' + [bool]$val + '  含CR(必须False)=' + $hit[0].Contains("`r") + '  长度与新口令一致=' + ($val.Length -eq $newLen))
    Remove-Variable val -Force -EA SilentlyContinue
    if ($hit.Count -ne 1) { Fail ($k + ' 命中 ' + $hit.Count + ' 行(应为 1); 重复键会让 dotenv 取值歧义') }
}
Remove-Variable lines, hit -Force -EA SilentlyContinue
if ($credObj -and $credPath) {
    $j = ([Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($credPath)) | ConvertFrom-Json)
    Write-Host ('  凭据 JSON  值非空=' + [bool]$j.password + '  长度与新口令一致=' + ([string]$j.password).Length + ' 等于 ' + $newLen)
    Remove-Variable j -Force -EA SilentlyContinue
}
Remove-Variable newP, newLen -Force -EA SilentlyContinue

Write-Host '== 9. 回滚 · 遗留清单 · 下一步 =='
Write-Host '  回滚(仅在需要退回旧口令时): 用旧口令跑本脚本的 -Manual 分支反向改回, 或'
Write-Host ('    Copy-Item ' + $bakEnv + ' ' + $EnvFile + ' -Force ; Restart-Service ' + $ServiceName + ' -Force')
if ($bakJson) { Write-Host ('    Copy-Item ' + $bakJson + ' ' + $credPath + ' -Force') }
Write-Host '  ⚠️ 回滚 .env 只是让落点退回旧口令, 线上口令不会自动跟着退回 —— 两者必须同时处置, 否则又是脱节。'
Write-Host ''
Write-Host '  【遗留 1】以下文件仍把该口令写成明文字面量, 轮换后会登录失败, 需另行改成从 env/凭据 JSON 读:'
Write-Host '    zhuxiang-jiu\backend\prod_activity_cend.py:35'
Write-Host '    zhuxiang-jiu\backend\prod_sms_deploy_verify.py:29'
Write-Host '    zhuxiang-jiu\backend\prod_stage_c_verify.py:29'
Write-Host '    zhuxiang-jiu\backend\verify_activity_cend.py:207'
Write-Host '    zhuxiang-jiu\backend\smoke_rpa_endpoint.sh:6   (用的还是更早的变体口令)'
Write-Host '    zhuxiang-jiu\backend\scripts\seed_redis.py:217 (种子本体; --force 会把线上口令刷回种子值)'
Write-Host '  【遗留 2】前端登录页的"一键预填"便利代码会失效(预填的是旧口令):'
Write-Host '    zhuxiang-jiu\login.html:258   taro-app\src\pages\login\index.tsx:522'
Write-Host '    taro-app\public\login.html:257   taro-app\dist\login.html:257   taro-app\dist\js\1945.*.js'
Write-Host '  【遗留 3】文档里的示例口令已过期(约 12 个 docs\*.md + 根 更新日志.md / 上线前检查清单.md)'
Write-Host ''
Write-Host '  下一步(回本地 PC):'
Write-Host '    1) 复跑全链路验证, 确认 e2e_auth_helper 用新 env 仍能登录:'
Write-Host '       powershell -File deploy\aliyun-ecs-rpc.ps1 Send -CommandFile deploy\autopublish_verify.ps1 -CommandName auto-verify -RegionId cn-beijing -InstanceId i-2zebz5dnx9903xdsbi7m'
Write-Host '    2) 提交本脚本入库并推双远程(脚本不含口令字面量, 可以入仓)'
Write-Host '    3) 处置【遗留 1】的 6 个文件 —— 这是 t14 的范围, 不做完则验收脚本仍会拿旧口令打线上'
Write-Host 'ROTATE-CREDS-DONE'