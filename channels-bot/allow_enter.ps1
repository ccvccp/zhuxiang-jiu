# 微信授权弹窗自动允许(触摸注入+压力通道)
# 铁律链(2026-09-28 用户洞察实证): 微信全端校验 pointer 压力——
#   Web侧 CDP force=0.5 才过(默认0被拒, 列表页Vue压力校验同款);
#   Qt客户端 QEventPoint.pressure 同源校验, Win32 mouse_event 无压力字段(pressure=0)→全拒
#   → InjectTouchInput 触摸注入带 pressure(0-1024, 512=0.5归一) 对齐人手
# 协同: bot.js 点「微信快捷登录」后写 allow_signal.flag → 本 watcher 轮询弹窗
# 用法: powershell -ExecutionPolicy Bypass -File allow_enter.ps1 <maxWaitSeconds>
# 退出码: 0=触摸点击已生效(弹窗关闭)  2=超时  3=无微信进程

param([int]$MaxWait = 300)
$LOGF = Join-Path $PSScriptRoot 'allow_enter.log'
$WL = { param($m) $line = "[$(Get-Date -Format 'HH:mm:ss')] $m"; Write-Host $line; Add-Content -Path $LOGF -Value $line }

Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Collections.Generic;
public class TE {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lp);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr lp);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder sb, int max);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder sb, int max);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll", SetLastError=true)] public static extern bool InitializeTouchInjection(uint maxCount, uint dwMode);
  [DllImport("user32.dll", SetLastError=true)] public static extern bool InjectTouchInput(uint count, IntPtr contacts);
  [DllImport("user32.dll")] public static extern int GetSystemMetrics(int nIndex);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
  [StructLayout(LayoutKind.Sequential)] public struct POINTER_INFO {
    public uint pointerType;   // PT_TOUCH = 2
    public uint pointerId;
    public uint frameId;
    public uint pointerFlags;
    public IntPtr hWndTarget;
    public POINT ptPixelLocation;
    public POINT ptHimetricLocation;
    public POINT ptPixelLocationRaw;
    public POINT ptHimetricLocationRaw;
    public uint dwTime;
    public uint historyCount;
    public int InputChannel;
    public uint dwKeyStates;
    public ulong PerformanceCount;
  }
  [StructLayout(LayoutKind.Sequential)] public struct POINTER_TOUCH_INFO {
    public POINTER_INFO pointerInfo;
    public uint touchFlags;
    public uint touchMask;
    public RECT rcContact;
    public uint orientation;
    public uint pressure;      // 0-1024; 512 = 0.5 归一(对齐 CDP force=0.5)
  }
  public static List<string> Results = new List<string>();
  public static bool Cb(IntPtr h, IntPtr lp) {
    if (IsWindowVisible(h)) {
      var cn = new StringBuilder(256); GetClassName(h, cn, 256);
      if (cn.ToString().IndexOf("Qt51514QWindowIcon") >= 0) {
        RECT r; GetWindowRect(h, out r);
        var sb = new StringBuilder(256); GetWindowText(h, sb, 256);
        Results.Add(h.ToInt64() + "|" + sb.ToString() + "|" + r.Left + "," + r.Top + "," + (r.Right - r.Left) + "," + (r.Bottom - r.Top));
      }
    }
    return true;
  }
  public static List<string> SnapQt() { Results = new List<string>(); EnumWindows(Cb, IntPtr.Zero); return Results; }
}
"@
# 常量: POINTER_FLAG
$FL_NEW = 0x1; $FL_INRANGE = 0x2; $FL_INCONTACT = 0x4; $FL_PRIMARY = 0x100; $FL_CONF = 0x400; $FL_DOWN = 0x10000; $FL_UPDATE = 0x20000; $FL_UP = 0x40000
$TM_CONTACTAREA = 0x1; $TM_ORIENTATION = 0x2; $TM_PRESSURE = 0x4; $TM_ALL = 0x7

$procs = Get-Process -Name Weixin -ErrorAction SilentlyContinue
if (-not $procs) { & $WL 'NO_WEIXIN_PROC'; exit 3 }
& $WL "weixin procs: $($procs.Count) (主窗托盘/隐藏均可, 弹窗按Qt类名枚举)"
$digcheck = [TE]::GetSystemMetrics(94)
& $WL "SM_DIGITIZER=$digcheck (0=无触摸硬件→键盘Enter回退, >0=触摸注入带压力512)"

# 阶段0: 等 bot 信号 flag
$flagPath = Join-Path $PSScriptRoot 'allow_signal.flag'
$flagDeadline = (Get-Date).AddSeconds($MaxWait)
$sawFlag = Test-Path $flagPath
if ($sawFlag) { Remove-Item $flagPath -Force -ErrorAction SilentlyContinue; & $WL 'flag already present (cleared), enter popup watch' }
while (-not $sawFlag -and (Get-Date) -lt $flagDeadline) {
  Start-Sleep -Milliseconds 800
  if (Test-Path $flagPath) { Remove-Item $flagPath -Force -ErrorAction SilentlyContinue; $sawFlag = $true; & $WL 'flag seen, enter popup watch' }
}
if (-not $sawFlag) { & $WL 'FLAG_TIMEOUT (bot 未触发快捷登录)'; exit 2 }

# 触摸注入初始化
$touchInit = [TE]::InitializeTouchInjection(2, 1)
& $WL "InitializeTouchInjection ok=$touchInit"
$touchAlloc = [Runtime.InteropServices.Marshal]::AllocHGlobal([Runtime.InteropServices.Marshal]::SizeOf([type][TE+POINTER_TOUCH_INFO]))

function TouchClick($x, $y, $press) {
  $info = New-Object TE+POINTER_TOUCH_INFO
  $info.pointerInfo.pointerType = 2
  $info.pointerInfo.pointerId = 1
  $info.pointerInfo.ptPixelLocation.X = [int]$x
  $info.pointerInfo.ptPixelLocation.Y = [int]$y
  # himetric 坐标 (0.01mm): px * 2540/96 ≈ px*26.46 —— 部分实现要求非零有效值
  $info.pointerInfo.ptHimetricLocation.X = [int]($x * 26.46)
  $info.pointerInfo.ptHimetricLocation.Y = [int]($y * 26.46)
  $info.touchMask = $TM_ALL
  $info.pressure = $press
  $info.orientation = 90
  $info.rcContact.Left = [int]($x - 6); $info.rcContact.Right = [int]($x + 6)
  $info.rcContact.Top = [int]($y - 6); $info.rcContact.Bottom = [int]($y + 6)
  # down (官方 Touch Injection 示例组合: DOWN|INRANGE|INCONTACT|PRIMARY, 无 NEW)
  $info.pointerInfo.pointerFlags = $FL_DOWN -bor $FL_INRANGE -bor $FL_INCONTACT -bor $FL_PRIMARY -bor $FL_CONF
  [Runtime.InteropServices.Marshal]::StructureToPtr($info, $touchAlloc, $false)
  $d1 = [TE]::InjectTouchInput(1, $touchAlloc)
  $e1 = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
  Start-Sleep -Milliseconds 120
  # up (压力归零模拟抬起)
  $info.pointerInfo.pointerFlags = $FL_INRANGE -bor $FL_UP -bor $FL_PRIMARY -bor $FL_CONF
  $info.pressure = 0
  [Runtime.InteropServices.Marshal]::StructureToPtr($info, $touchAlloc, $false)
  $d2 = [TE]::InjectTouchInput(1, $touchAlloc)
  $e2 = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
  return "$d1/$d2 err=$e1/$e2"
}

$deadline = (Get-Date).AddSeconds(150)
$handled = @{}
while ((Get-Date) -lt $deadline) {
  $wins = [TE]::SnapQt()
  $popup = $null
  foreach ($win in $wins) {
    $p = $win.Split('|')
    if ($handled.ContainsKey($p[0])) { continue }
    $wh = $p[2].Split(',')
    $wid2 = [int]$wh[2]; $hei2 = [int]$wh[3]
    if ($p[1] -match '登录|视频号' -or ($wid2 -ge 340 -and $wid2 -le 410 -and $hei2 -ge 240 -and $hei2 -le 300)) { $popup = $p; break }
  }
  if ($popup) {
    $hwnd = [IntPtr][Int64]$popup[0]
    $wh2 = $popup[2].Split(',')
    # 「允许」按钮中心 = 弹窗左上角 + (101, 230) (截图+像素双校准)
    $cx = [int]$wh2[0] + 101; $cy = [int]$wh2[1] + 230
    # 触摸硬件探测: InjectTouchInput 需 digitizer(无触摸屏机器 err=87 物理不可用)
    $digitizer = $digcheck
    & $WL "popup: $($popup[1]) rect=$($popup[2]) digitizer=$digitizer -> allow at $cx,$cy"
    Start-Sleep -Milliseconds 2500
    $done = $false
    if ($digitizer -gt 0) {
      # 触摸点击 3 轮 (带压力 512 = 0.5 归一, 对齐 CDP force=0.5)
      for ($i = 1; $i -le 3; $i++) {
        $res = TouchClick $cx $cy 512
        & $WL "touch round $i inject=$res"
        Start-Sleep -Milliseconds 2500
        $after = [TE]::SnapQt()
        if (-not ($after | Where-Object { $_ -eq ($popup -join '|') })) { & $WL "TOUCH_OK (round $i)"; exit 0 }
        & $WL "touch round $i not effective, retry..."
      }
    } else {
      & $WL 'no touch hardware -> keyboard Enter fallback'
      for ($i = 1; $i -le 3; $i++) {
        # Alt trick 解锁前台权 (后台 spawn 进程无前台权, SetForegroundWindow 静默失败)
        [TE]::keybd_event(0x12, 0x38, 0, [UIntPtr]::Zero)
        [TE]::keybd_event(0x12, 0x38, 2, [UIntPtr]::Zero)
        Start-Sleep -Milliseconds 120
        [TE]::SetForegroundWindow($hwnd) | Out-Null
        Start-Sleep -Milliseconds 600
        [TE]::keybd_event(0x0D, 0x1C, 0, [UIntPtr]::Zero)
        Start-Sleep -Milliseconds 80
        [TE]::keybd_event(0x0D, 0x1C, 2, [UIntPtr]::Zero)
        Start-Sleep -Milliseconds 2500
        $after = [TE]::SnapQt()
        if (-not ($after | Where-Object { $_ -eq ($popup -join '|') })) { & $WL "ENTER_OK (round $i)"; exit 0 }
        & $WL "enter round $i not effective, retry..."
      }
    }
    $handled[$popup[0]] = $true
    & $WL 'not effective on this hwnd, mark handled and keep watching'
  }
  Start-Sleep -Milliseconds 1000
}
& $WL 'POPUP_TIMEOUT'
exit 2
