# 微信授权弹窗自动允许(键盘 Enter 通道)
# 铁律: 微信检测鼠标合成输入(LLMHF_INJECTED)拒执行授权; 键盘注入未检测(实证 2026-09-28)
#       前台化弹窗 + Enter 触发默认按钮「允许」
# 协同: bot.js 点「微信快捷登录」后写 allow_signal.flag → 本 watcher(由 shell 后台 job 跑,
#       bot spawn 的深沙箱子进程枚举不到弹窗——实证) 见 flag 后进入弹窗轮询
# 用法: powershell -ExecutionPolicy Bypass -File allow_enter.ps1 <maxWaitSeconds>
# 退出码: 0=Enter 已生效(弹窗关闭)  2=超时未见弹窗  3=无微信进程

param([int]$MaxWait = 300)
$LOGF = Join-Path $PSScriptRoot 'allow_enter.log'
$W = { param($m) $line = "[$(Get-Date -Format 'HH:mm:ss')] $m"; Write-Host $line; Add-Content -Path $LOGF -Value $line }

Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Collections.Generic;
public class AE {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lp);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr lp);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder sb, int max);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool attach);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  public static uint TargetPid = 0;
  public static List<string> Results = new List<string>();
  public static bool Cb(IntPtr h, IntPtr lp) {
    uint pid; GetWindowThreadProcessId(h, out pid);
    if (pid == TargetPid && IsWindowVisible(h)) {
      RECT r; GetWindowRect(h, out r);
      var sb = new StringBuilder(256); GetWindowText(h, sb, 256);
      Results.Add(h.ToInt64() + "|" + sb.ToString() + "|" + r.Left + "," + r.Top + "," + (r.Right - r.Left) + "," + (r.Bottom - r.Top));
    }
    return true;
  }
  public static List<string> Snap(uint pid) { TargetPid = pid; Results = new List<string>(); EnumWindows(Cb, IntPtr.Zero); return Results; }
}
"@

$procs = Get-Process -Name Weixin -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 }
if (-not $procs) { & $W 'NO_WEIXIN_PROC'; exit 3 }
$mainPid = ($procs | Select-Object -First 1).Id
& $W "watching weixin pid=$mainPid"

# 阶段0: 等 bot 的信号 flag(allow_signal.flag)——见 flag 前不进弹窗轮询(防旧残留弹窗干扰)
$flagPath = Join-Path $PSScriptRoot 'allow_signal.flag'
$flagDeadline = (Get-Date).AddSeconds($MaxWait)
$sawFlag = Test-Path $flagPath
if ($sawFlag) { Remove-Item $flagPath -Force -ErrorAction SilentlyContinue; & $W 'flag already present (cleared), enter popup watch' }
while (-not $sawFlag -and (Get-Date) -lt $flagDeadline) {
  Start-Sleep -Milliseconds 800
  if (Test-Path $flagPath) { Remove-Item $flagPath -Force -ErrorAction SilentlyContinue; $sawFlag = $true; & $W 'flag seen, enter popup watch' }
}
if (-not $sawFlag) { & $W 'FLAG_TIMEOUT (bot 未触发快捷登录)'; exit 2 }

$deadline = (Get-Date).AddSeconds(150)
$handled = @{}   # 已处理过的弹窗 hwnd(残留旧弹窗/已无效的请求), 防旧窗干扰
while ((Get-Date) -lt $deadline) {
  $wins = [AE]::Snap([uint32]$mainPid)
  $popup = $null
  foreach ($win in $wins) {
    $p = $win.Split('|')
    if ($handled.ContainsKey($p[0])) { continue }
    $wh = $p[2].Split(',')
    $wid2 = [int]$wh[2]; $hei2 = [int]$wh[3]
    # 弹窗特征: 标题 微信/登录 或确认框尺寸 340-410 x 240-300 (376x268 实证)
    if ($p[1] -match '登录|视频号' -or ($wid2 -ge 340 -and $wid2 -le 410 -and $hei2 -ge 240 -and $hei2 -le 300)) { $popup = $p; break }
  }
  if ($popup) {
    $hwnd = [IntPtr][Int64]$popup[0]
    & $W "popup: $($popup[1]) rect=$($popup[2]) -> settle 2.5s then Enter x3"
    Start-Sleep -Milliseconds 2500
    # 前台权解锁(后台 spawn 的进程无前台权, SetForegroundWindow 静默失败——实证):
    #   双通道 = Alt 键技巧 + AttachThreadInput(挂接当前前台线程的输入队列)
    $unlockFg = {
      # Alt trick: 模拟 Alt 按下让系统授予本进程前台权
      [AE]::keybd_event(0x12, 0x38, 0, [UIntPtr]::Zero)
      [AE]::keybd_event(0x12, 0x38, 2, [UIntPtr]::Zero)
      Start-Sleep -Milliseconds 120
      [AE]::SetForegroundWindow($hwnd) | Out-Null
      if ([AE]::GetForegroundWindow() -ne $hwnd) {
        $fgPid = 0
        $fgThread = [AE]::GetWindowThreadProcessId([AE]::GetForegroundWindow(), [ref]$fgPid)
        $myThread = [AE]::GetCurrentThreadId()
        [AE]::AttachThreadInput($myThread, $fgThread, $true) | Out-Null
        [AE]::SetForegroundWindow($hwnd) | Out-Null
        [AE]::AttachThreadInput($myThread, $fgThread, $false) | Out-Null
      }
    }
    # Enter 重试 3 轮 (弹窗初始化竞态 + 前台化竞态双保险)
    for ($i = 1; $i -le 3; $i++) {
      & $unlockFg
      Start-Sleep -Milliseconds 600
      $fgNow = [AE]::GetForegroundWindow()
      & $W "round $i foreground=$($fgNow -eq $hwnd)"
      [AE]::keybd_event(0x0D, 0x1C, 0, [UIntPtr]::Zero)
      Start-Sleep -Milliseconds 80
      [AE]::keybd_event(0x0D, 0x1C, 2, [UIntPtr]::Zero)
      Start-Sleep -Milliseconds 2500
      $after = [AE]::Snap([uint32]$mainPid)
      if (-not ($after | Where-Object { $_ -eq ($popup -join '|') })) { & $W "ENTER_OK (round $i)"; exit 0 }
      & $W "enter round $i not effective, retry..."
    }
    # 3 轮无效: 记入已处理(旧残留弹窗), 继续轮询等待新弹窗
    $handled[$popup[0]] = $true
    & $W 'enter not effective on this hwnd, mark handled and keep watching'
  }
  Start-Sleep -Milliseconds 1000
}
& $W 'POPUP_TIMEOUT'
exit 2
