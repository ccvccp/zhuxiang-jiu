# 微信登录确认弹窗探测器: 轮询 Weixin 新可见窗口 → 全屏截图 + dump 窗口 rect
# 用法: powershell -File wait_popup.ps1 <maxWaitSeconds> <outPrefix>
param([int]$MaxWait = 60, [string]$Prefix = "popup")

Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Collections.Generic;
public class W {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lp);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr lp);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder sb, int max);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder sb, int max);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  public static uint TargetPid = 0;
  public static List<string> Results = new List<string>();
  public static bool Cb(IntPtr h, IntPtr lp) {
    uint pid; GetWindowThreadProcessId(h, out pid);
    if (pid == TargetPid && IsWindowVisible(h)) {
      var sb = new StringBuilder(256); GetWindowText(h, sb, 256);
      var cn = new StringBuilder(256); GetClassName(h, cn, 256);
      RECT r; GetWindowRect(h, out r);
      Results.Add(h.ToInt64() + "|" + sb.ToString() + "|" + cn.ToString() + "|" + r.Left + "," + r.Top + "," + (r.Right - r.Left) + "," + (r.Bottom - r.Top));
    }
    return true;
  }
  public static List<string> Snap(uint pid) {
    TargetPid = pid; Results = new List<string>();
    EnumWindows(Cb, IntPtr.Zero);
    return Results;
  }
}
"@

Add-Type -AssemblyName System.Drawing

# 目标: Weixin 主进程 (有主窗口的那个)
$procs = Get-Process -Name Weixin -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 }
if (-not $procs) { Write-Host "NO_WEIXIN_PROC"; exit 1 }
$mainPid = ($procs | Select-Object -First 1).Id
Write-Host "target weixin pid=$mainPid"

$baseline = [W]::Snap([uint32]$mainPid)
Write-Host "baseline windows: $($baseline.Count)"
$baseline | ForEach-Object { Write-Host "  BASE $_" }

$deadline = (Get-Date).AddSeconds($MaxWait)
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Milliseconds 1200
  $cur = [W]::Snap([uint32]$mainPid)
  $new = $cur | Where-Object { $baseline -notcontains $_ }
  if ($new) {
    Write-Host "NEW WINDOW DETECTED:"
    $new | ForEach-Object { Write-Host "  NEW $_" }
    Start-Sleep -Milliseconds 1500
    Add-Type -AssemblyName System.Windows.Forms
    $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen(0, 0, 0, 0, $bmp.Size)
    $shot = "d:\网站架构设计\channels-bot\${Prefix}_screen.png"
    $bmp.Save($shot, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
    Write-Host "shot saved: $shot"
    Write-Host "ALL_WINDOWS_NOW:"
    $cur | ForEach-Object { Write-Host "  NOW $_" }
    exit 0
  }
}
Write-Host "POPUP_TIMEOUT (no new weixin window in ${MaxWait}s)"
exit 2
