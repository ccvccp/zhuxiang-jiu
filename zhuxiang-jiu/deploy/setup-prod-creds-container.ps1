# t4-B 生产登录凭据注入(backend 容器侧) —— 在 ECS 主机上运行, 操作 docker
# 用法(ECS 上): powershell -ExecutionPolicy Bypass -File D:\zhuxiang\zhuxiang-jiu\deploy\setup-prod-creds-container.ps1
#   镜像仍是旧版(容器内无 prod_creds.py)时加 -Build:
#   powershell -ExecutionPolicy Bypass -File D:\zhuxiang\zhuxiang-jiu\deploy\setup-prod-creds-container.ps1 -Build
# 前置: 先跑 A 段 setup-prod-creds-host.ps1(建好 ZX_CRED_FILE); 本脚本读不到时会交互兜底
# 为什么必须做: e2e_auth_helper.py 已改 fail-closed(commit b83ad7ae), 容器内无 PROD_ADMIN_*
#   时所有验收脚本登录直接 raise => 全链路不发(依据 .trae/rules/security-hard-coded.md 二.3)
# 关键语义: docker restart 【不会】重读 env_file/compose env, 必须 compose up -d 重建容器;
#   docker cp 进容器的文件在下次 recreate 时丢失(只是热补丁)
# 幂等: 可重复运行; 覆盖 .env 前先备份(依据 二.4 备份先于写入)
param(
    [string]$RepoRoot = 'D:\zhuxiang',
    [string]$BackendFilter = 'backend',
    [switch]$Build
)
$ErrorActionPreference = 'Continue'

function Fail([string]$msg) {
    Write-Host ('  [FAIL] ' + $msg)
    Write-Host 'CONTAINER-CREDS-FAILED'
    exit 1
}
function Mask([string]$s) {
    if ([string]::IsNullOrEmpty($s)) { return '<empty>' }
    if ($s.Length -le 5) { return '****' }
    return $s.Substring(0, 3) + '****' + $s.Substring($s.Length - 2)
}

Write-Host '== 0. 定位 compose 根与 docker CLI =='
$composeFile = Join-Path $RepoRoot 'docker-compose.yml'
if (-not (Test-Path $composeFile)) {
    Fail ('未找到 ' + $composeFile + '; 请用 -RepoRoot 指定 ECS 上实际仓库根(默认 D:\zhuxiang)')
}
$envFile = Join-Path $RepoRoot '.env'
Write-Host ('  compose 文件: ' + $composeFile)
Write-Host ('  目标 .env   : ' + $envFile + '   已存在: ' + (Test-Path $envFile))

# compose CLI 探测: 新版是 `docker compose`(插件), 旧版是 `docker-compose`(独立二进制)
# 刻意用两个独立变量($dcExe + $dcArgs)而非数组切片: 单元素数组时 $a[1..0] 会产生 @($null,...)
$dcExe = $null; $dcArgs = @()
docker compose version 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) { $dcExe = 'docker'; $dcArgs = @('compose') }
else {
    docker-compose version 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $dcExe = 'docker-compose'; $dcArgs = @() }
    else { Fail 'docker compose / docker-compose 均不可用; 先确认 Docker Desktop 或 compose 插件已装' }
}
Write-Host ('  compose 命令: ' + $dcExe + ' ' + ($dcArgs -join ' '))

Write-Host '== 1. 取凭据(ZX_CRED_FILE 优先, 缺失则交互兜底) =='
$credPath = [Environment]::GetEnvironmentVariable('ZX_CRED_FILE', 'Machine')
if (-not $credPath) { $credPath = $env:ZX_CRED_FILE }
$phone = $null; $passwd = $null
if ($credPath -and (Test-Path $credPath)) {
    # 逐字复刻 5 个 .ps1 内联 Get-Sv73Cred 的读法: ReadAllBytes + UTF8.GetString(不剥 BOM)
    $o = ([Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($credPath)) | ConvertFrom-Json)
    $phone = [string]$o.phone
    $passwd = [string]$o.password
    Write-Host ('  来源: ' + $credPath)
    Remove-Variable o -Force -EA SilentlyContinue
} else {
    Write-Host '  [警告] ZX_CRED_FILE 未登记或文件不存在 —— 改为交互输入(建议先跑 A 段, 避免口令散落)'
    # 注意: 名为 pwd 的变量是 PowerShell 内置自动变量(= 当前目录), 绝不可用作口令变量名, 故用 passwd
    $phone = (Read-Host '  站点管理员手机号 (须纯数字)').Trim()
    $s1 = Read-Host '  登录口令 (输入不回显)' -AsSecureString
    $s2 = Read-Host '  再输一次确认' -AsSecureString
    $b1 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s1)
    $b2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s2)
    try {
        $t1 = [Runtime.InteropServices.Marshal]::PtrToStringAuto($b1)
        $t2 = [Runtime.InteropServices.Marshal]::PtrToStringAuto($b2)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b1)
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b2)
        Remove-Variable s1, s2 -Force -EA SilentlyContinue
    }
    if ($t1 -ne $t2) { Remove-Variable t1, t2 -Force -EA SilentlyContinue; Fail '两次口令不一致; fail-closed 退出, 未写入任何内容' }
    $passwd = $t1
    Remove-Variable t1, t2 -Force -EA SilentlyContinue
}
$member = [Environment]::GetEnvironmentVariable('E2E_MEMBER_PHONE', 'Machine')
if (-not $member) { $member = $env:E2E_MEMBER_PHONE }
if (-not $member) { $member = $phone }

if ($phone -notmatch '^\d+$') { Fail ('手机号字符集非法(须纯数字): ' + (Mask $phone)) }
if ($member -notmatch '^\d+$') { Fail ('会员手机号字符集非法(须纯数字): ' + (Mask $member)) }
if ($passwd -notmatch '^[A-Za-z0-9]+$') {
    Remove-Variable passwd -Force -EA SilentlyContinue
    Fail '口令字符集非法(须仅字母数字) —— 含 $ 反引号 分号 等符号在远端 pipe 拼接里就是注入面(契约见 backend\.env.example:372)'
}
$pwdLen = $passwd.Length
Write-Host ('  管理员号: ' + (Mask $phone) + '   会员号: ' + (Mask $member) + '   口令长度: ' + $pwdLen)

Write-Host '== 2. 备份并改写 compose 根 .env =='
if (Test-Path $envFile) {
    $bak = $envFile + '.bak.' + (Get-Date -Format 'yyyyMMddHHmmss')
    Copy-Item $envFile $bak -Force
    Write-Host ('  已备份: ' + (Split-Path $bak -Leaf) + ' (' + (Get-Item $bak).Length + ' bytes)')
} else {
    Write-Host '  .env 不存在, 将新建(compose 的 env_file 标了 required: false, 有无都合法)'
}
# 字段名对齐 prod_creds.py 的 _ADMIN_PHONE_NAMES/_PASSWORD_NAMES/_MEMBER_PHONE_NAMES:
#   PROD_ADMIN_* 是首选名, ZX_API_* 是同义回退名(两套都写, 覆盖两类消费方)
$keys = [ordered]@{
    'PROD_ADMIN_PHONE'    = $phone
    'PROD_ADMIN_PASSWORD' = $passwd
    'ZX_API_PHONE'        = $phone
    'ZX_API_PASSWORD'     = $passwd
    'E2E_MEMBER_PHONE'    = $member
}
$oldLines = @()
if (Test-Path $envFile) { $oldLines = @([IO.File]::ReadAllLines($envFile)) }
$patterns = @($keys.Keys | ForEach-Object { '^\s*' + [regex]::Escape($_) + '\s*=' })
# 同名键后写覆盖前值 => 必须先删旧行再追加, 否则 .env 里会留两份造成误读
$kept = @($oldLines | Where-Object { $l = $_; -not ($patterns | Where-Object { $l -match $_ }) })
$newLines = @($keys.Keys | ForEach-Object { $_ + '=' + $keys[$_] })
$stamp = '# ---- t4-B 生产登录凭据注入(prod_creds.py / e2e_auth_helper.py fail-closed 依赖; ' + (Get-Date -Format 'yyyy-MM-dd HH:mm') + ') ----'
$all = @($kept) + @($stamp) + @($newLines)
# 关键: LF 行尾 + 无 BOM。PS5.1 Add-Content/Set-Content 默认写 CRLF; 若 compose 的 env_file
#       解析不 trim \r, 口令尾部会多一个 \r => 登录失败。故用 WriteAllText 手工拼 LF。
[IO.File]::WriteAllText($envFile, (($all -join "`n") + "`n"), (New-Object Text.UTF8Encoding $false))
Remove-Variable passwd -Force -EA SilentlyContinue
$fi = Get-Item $envFile
Write-Host ('  保留 ' + $kept.Count + ' 行 + 新增 5 键; 总 ' + $all.Count + ' 行   文件字节数: ' + $fi.Length)
Write-Host ('  含CR(应为 False): ' + (([IO.File]::ReadAllText($envFile)).Contains("`r")))
Write-Host ('  含BOM(应为 False): ' + ($fi.Length -ge 3 -and ([IO.File]::ReadAllBytes($envFile))[0] -eq 0xEF))

if (Test-Path (Join-Path $RepoRoot '.git')) {
    & git -C $RepoRoot check-ignore -q .env
    if ($LASTEXITCODE -eq 0) { Write-Host '  .gitignore 覆盖 .env: True(密钥不入仓)' }
    else { Fail '.env 未被 .gitignore 忽略 —— 存在密钥入仓风险; 先在 .gitignore 加 /.env 再重跑' }
} else {
    Write-Host '  [警告] ' + $RepoRoot + ' 非 git 仓库, 跳过 check-ignore 核验'
}

Write-Host '== 3. 重建 backend 容器(使 env_file 生效) =='
Push-Location $RepoRoot
try {
    $upArgs = $dcArgs + @('up', '-d')
    if ($Build) { $upArgs += '--build' }
    $upArgs += 'backend'
    Write-Host ('  执行: ' + $dcExe + ' ' + ($upArgs -join ' '))
    & $dcExe $upArgs
    $upExit = $LASTEXITCODE
} finally { Pop-Location }
if ($upExit -ne 0) { Fail ('compose up 失败(exit ' + $upExit + '), 凭据未生效; 回滚可用第 2 步的 .bak 文件') }

Write-Host '== 4. 重新发现 backend 容器实名 =='
Start-Sleep -Seconds 5
$names = @(docker ps --format '{{.Names}}')
$cand = @($names | Where-Object { $_ -match $BackendFilter })
if ($cand.Count -eq 0) { Fail ('重建后仍无匹配 "' + $BackendFilter + '" 的运行中容器; docker ps 现有: ' + ($names -join ', ')) }
$BE = $cand[0]
Write-Host ('  容器: ' + $BE + '   状态: ' + (docker inspect -f '{{.State.Status}}' $BE))

Write-Host '== 5. 容器内验证(只回显长度/布尔, 不回显值) =='
# docker exec 不经 shell, PS 把下面每个单引号串作为单个 argv 元素传给容器内 python -c
# => 片段内部一律用双引号, 无注入面
$py1 = 'import os;v=os.environ.get("PROD_ADMIN_PASSWORD","");print("  pwd_len",len(v),"pwd_alnum",v.isalnum())'
$out1 = (docker exec $BE python -c $py1 2>&1 | Out-String).Trim()
Write-Host $out1
if ($out1 -notmatch ('pwd_len\s+' + $pwdLen + '\b')) {
    Write-Host '  [警告] 容器内口令长度与主机侧不一致 —— 极可能是 CRLF 污染(env_file 解析未 trim \r)或 .env 未被容器读到'
}
if ($out1 -notmatch 'pwd_alnum\s+True') { Write-Host '  [警告] 口令尾部有 \r 或非法字符' }

$py2 = 'import prod_creds as p;a=p.admin_phone();w=p.login_password();m=p.member_phone();print("  prod_creds OK phone_digits",a.isdigit(),"pwd_len",len(w),"member_digits",m.isdigit())'
$out2 = (docker exec $BE python -c $py2 2>&1 | Out-String).Trim()
Write-Host $out2
if ($out2 -match 'ModuleNotFoundError|No module named') {
    Write-Host '  [FAIL] 容器内无 prod_creds.py —— 镜像仍是 b83ad7ae 之前的版本'
    Write-Host '         修法一(推荐, 烤进镜像): 重跑本脚本并加 -Build'
    Write-Host '         修法二(热补丁, 下次 recreate 会丢):'
    Write-Host ('           docker cp ' + $RepoRoot + '\zhuxiang-jiu\backend\prod_creds.py ' + $BE + ':/app/')
    Write-Host ('           docker cp ' + $RepoRoot + '\zhuxiang-jiu\backend\e2e_auth_helper.py ' + $BE + ':/app/')
}

$py3 = 'import e2e_auth_helper as h;print("  helper_ok",hasattr(h,"E2EAuth"))'
Write-Host ((docker exec $BE python -c $py3 2>&1 | Out-String).Trim())

# 最强验证: 容器内真登录(打本地 backend, 不出网) —— 成功即 fail-closed 解除
$py4 = 'import httpx,prod_creds as p;c=httpx.Client(base_url="http://127.0.0.1:8000",timeout=20);ph,pw=p.admin_creds();r=c.post("/api/auth/login",json={"phone":ph,"password":pw});j=r.json() if r.status_code==200 else {};print("  login_status",r.status_code,"has_token",bool(j.get("accessToken")))'
Write-Host ((docker exec $BE python -c $py4 2>&1 | Out-String).Trim())

Write-Host '== 6. 下一步(C 段 · 全链路复跑) =='
Write-Host '  B 段完成。回本地 PC 复跑自动发布完整性验证:'
Write-Host '    powershell -File deploy\aliyun-ecs-rpc.ps1 Send -CommandFile deploy\autopublish_verify.ps1 -CommandName auto-verify -RegionId cn-beijing -InstanceId i-2zebz5dnx9903xdsbi7m'
Write-Host '  期望第 5 段「队列余量(API)」不再出现: API fail: ZX_API_PHONE/ZX_API_PASSWORD 未配置'
Write-Host 'CONTAINER-CREDS-DONE'
