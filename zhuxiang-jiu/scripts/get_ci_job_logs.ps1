# 通过 Windows 凭据管理器 CredRead 获取 GitHub API token 下载 job 日志(不回显 token)
param(
    [Parameter(Mandatory = $true)][long]$JobId
)
$ErrorActionPreference = "Stop"

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class CredMan {
    [DllImport("advapi32.dll", EntryPoint = "CredReadW", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool CredRead(string target, int type, int reservedFlag, out IntPtr credentialPtr);
    [DllImport("advapi32.dll", EntryPoint = "CredFree", SetLastError = true)]
    public static extern void CredFree(IntPtr credential);
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct CREDENTIAL {
        public int Flags;
        public int Type;
        public string TargetName;
        public string Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public int CredentialBlobSize;
        public IntPtr CredentialBlob;
        public int Persist;
        public int AttributeCount;
        public IntPtr Attributes;
        public string TargetAlias;
        public string UserName;
    }
}
"@

$target = "GitHub - https://api.github.com/ccvccp"
$ptr = [IntPtr]::Zero
if (-not [CredMan]::CredRead($target, 1, 0, [ref]$ptr)) {
    Write-Output "CRED_NOT_FOUND"
    exit 1
}
$cred = [System.Runtime.InteropServices.Marshal]::PtrToStructure($ptr, [type][CredMan+CREDENTIAL])
$bytes = New-Object byte[] $cred.CredentialBlobSize
[System.Runtime.InteropServices.Marshal]::Copy($cred.CredentialBlob, $bytes, 0, $cred.CredentialBlobSize)
# token 存储为 UTF-16 或 ASCII, 双尝试
$token = [System.Text.Encoding]::UTF8.GetString($bytes)
if ($token -notmatch "^[\x21-\x7e]+$") {
    $token = [System.Text.Encoding]::Unicode.GetString($bytes).TrimEnd([char]0)
}
[CredMan]::CredFree($ptr)
if (-not $token) { Write-Output "TOKEN_EMPTY"; exit 1 }

$req = [System.Net.HttpWebRequest]::Create(
    "https://api.github.com/repos/ccvccp/zhuxiang-jiu/actions/jobs/$JobId/logs")
$req.Method = "GET"
$req.Accept = "application/vnd.github+json"
$req.UserAgent = "ci-log-fetcher"
$req.Headers.Add("Authorization", "Bearer $token")

try {
    $resp = $req.GetResponse()
    $zipPath = Join-Path $env:TEMP "job-$JobId-logs.zip"
    $file = [System.IO.File]::Create($zipPath)
    $resp.GetResponseStream().CopyTo($file)
    $file.Close()
    $outDir = "$env:TEMP\job-$JobId-logs"
    if (Test-Path $outDir) { Remove-Item -Recurse -Force $outDir }
    Expand-Archive -Path $zipPath -DestinationPath $outDir -Force
    Write-Output "SAVED_DIR: $outDir"
    Get-ChildItem $outDir -Recurse -File | Select-Object -ExpandProperty FullName
} catch {
    Write-Output "HTTP_ERROR: $($_.Exception.Message)"
    exit 1
}
