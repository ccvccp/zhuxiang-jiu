# 73号(sv) 黄金时段发布队列自动转态——每日 18:01(北京时间)
# queued(scheduledAt<=now) → rpa_pending; 输出留档 publish_process.log
# RPA 发布段不在本任务内(由 sv73_autopublish.ps1 自动消费, 2026-10-23 起服务器无人值守)
# 2026-10-11 迁国内: SSH 海外 docker 链已替换为国内生产 API(https://zyjiu.com)
# 2026-10-23 可移植化: 日志路径改脚本目录相对(迁新机 C:\channels-bot 零改动)
$log = Join-Path $PSScriptRoot 'publish_process.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

# ---- UTF-8 安全 REST ----
# PS5.1 的 Invoke-RestMethod 在响应头缺 charset 时按 ISO-8859-1 解码 body, 中文全变乱码。
# 取原始字节自行按 UTF-8 解, 请求体也一律以 UTF-8 字节发送。
function Invoke-Json([string]$Uri, [string]$Method = 'Get', $Headers = $null, $BodyObj = $null, [int]$TimeoutSec = 30) {
    $pa = @{ Uri = $Uri; Method = $Method; UseBasicParsing = $true; TimeoutSec = $TimeoutSec }
    if ($Headers) { $pa.Headers = $Headers }
    if ($null -ne $BodyObj) {
        $pa.ContentType = 'application/json; charset=utf-8'
        $pa.Body = [Text.Encoding]::UTF8.GetBytes(($BodyObj | ConvertTo-Json -Depth 5 -Compress))
    }
    $resp = Invoke-WebRequest @pa
    $raw = [Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray())
    if (-not $raw.Trim()) { return $null }
    return ($raw | ConvertFrom-Json)
}
# ---- 生产 API 凭据(禁止硬编码; 依据 .trae/rules/security-hard-coded.md 一.2) ----
# 读取顺序: 环境变量 ZX_API_PHONE / ZX_API_PASSWORD
#           -> ZX_CRED_FILE 指向的 JSON(仓外, 形如 {"phone":"","password":""})
# 缺失即 throw(fail-closed): 宁可不发, 不把口令写进源码/日志/提交历史。
function Get-Sv73Cred {
    $ph = $env:ZX_API_PHONE; $pw = $env:ZX_API_PASSWORD
    if (-not ($ph -and $pw) -and $env:ZX_CRED_FILE -and (Test-Path $env:ZX_CRED_FILE)) {
        $o = ([Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($env:ZX_CRED_FILE)) | ConvertFrom-Json)
        $ph = $o.phone; $pw = $o.password
    }
    if (-not ($ph -and $pw)) { throw 'ZX_API_PHONE/ZX_API_PASSWORD 未配置(ZX_CRED_FILE 亦缺失), fail-closed 退出' }
    return @{ phone = $ph; password = $pw }
}

try {
    $BASE = 'https://zyjiu.com'
    $login = Invoke-Json "$BASE/api/auth/login" Post $null (Get-Sv73Cred) 20
    $h = @{ Authorization = "Bearer $($login.accessToken)"; 'X-Role' = 'admin' }
    $res = Invoke-Json "$BASE/api/promo/publish/process" Post $h @{} 30
    $lines = @()
    foreach ($c in @($res.data)) {
        $mode = ''
        if ($c.receipt) { $mode = $c.receipt.mode }
        $lines += "$($c.contentId) $($c.status) $mode"
    }
    $lines += "DONE $($lines.Count)"
    $out = $lines -join "`n"
} catch {
    $out = "FAIL: $($_.Exception.Message)"
    # 失败告警钩子(2026-10-11): 桌面弹窗 + flag 文件 + 事件日志
    $stamp2 = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path (Join-Path (Split-Path $log -Parent) 'PUBLISH_FAIL_ALERT.flag') -Value "[$stamp2] process-task FAILED: $($_.Exception.Message)" -Encoding UTF8
    try { cmd /c "msg * /time:90 sv73 TRANSITION FAILED :: $($_.Exception.Message)" } catch {}
    try { cmd /c "eventcreate /t ERROR /id 731 /l APPLICATION /d ""sv73 transition FAILED: $($_.Exception.Message)"" " } catch {}
}
"$stamp`n$out" | Out-File -FilePath $log -Append -Encoding utf8
