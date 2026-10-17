Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Collections.Generic;
public class WEnum {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lp);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr lp);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder sb, int max);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder sb, int max);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  public static List<string> Results = new List<string>();
  public static bool Cb(IntPtr h, IntPtr lp) {
    if (IsWindowVisible(h)) {
      uint pid; GetWindowThreadProcessId(h, out pid);
      var sb = new StringBuilder(256); GetWindowText(h, sb, 256);
      var cn = new StringBuilder(256); GetClassName(h, cn, 256);
      RECT r; GetWindowRect(h, out r);
      Results.Add(pid + "|" + sb.ToString() + "|" + cn.ToString() + "|" + r.Left + "," + r.Top + "," + (r.Right - r.Left) + "," + (r.Bottom - r.Top));
    }
    return true;
  }
  public static List<string> Snap() { Results = new List<string>(); EnumWindows(Cb, IntPtr.Zero); return Results; }
}
"@
$all = [WEnum]::Snap()
Write-Host "=== all visible top-level windows (pid|title|class|rect) ==="
$all | ForEach-Object { Write-Host $_ }
Write-Host "=== weixin pids ==="
Get-Process -Name Weixin -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "$($_.Id) $($_.Path)" }
