<#
screenshot.ps1 —— 截取指定标题（子串匹配）的窗口为 PNG。

用途：agent 无法点鼠标，但可以把真窗口的画面存成图片，交给能看图的模型
（或交由人查看）。只截该窗口矩形，不截整个桌面（避免拍到无关内容）。

用法:
    powershell -ExecutionPolicy Bypass -File tools\screenshot.ps1 `
        -Title "坠毁ASI" -Out saves\shot.png
#>
param(
    [string]$Title = "坠毁ASI",
    [string]$Out = "",
    [switch]$WholeScreen,
    [int]$DelayMs = 400
)

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class DshWinCap {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  public struct RECT { public int Left, Top, Right, Bottom; }
  public static IntPtr Find(string part) {
    IntPtr found = IntPtr.Zero;
    EnumWindows(delegate(IntPtr h, IntPtr l) {
      if (!IsWindowVisible(h)) return true;
      StringBuilder sb = new StringBuilder(512);
      GetWindowText(h, sb, 512);
      if (sb.ToString().Contains(part)) { found = h; return false; }
      return true;
    }, IntPtr.Zero);
    return found;
  }
}
"@ -ErrorAction Stop

if ([string]::IsNullOrWhiteSpace($Out)) {
    $Out = Join-Path (Split-Path -Parent $PSScriptRoot) "saves\shot.png"
}
$dir = Split-Path -Parent $Out
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }

if ($WholeScreen) {
    $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
    $x = $b.Left; $y = $b.Top; $w = $b.Width; $h = $b.Height
} else {
    $hWnd = [DshWinCap]::Find($Title)
    if ($hWnd -eq [IntPtr]::Zero) {
        Write-Output "WINDOW-NOT-FOUND: '$Title'"
        exit 1
    }
    $r = New-Object DshWinCap+RECT
    [void][DshWinCap]::GetWindowRect($hWnd, [ref]$r)
    [void][DshWinCap]::SetForegroundWindow($hWnd)
    Start-Sleep -Milliseconds $DelayMs
    $x = $r.Left; $y = $r.Top
    $w = $r.Right - $r.Left; $h = $r.Bottom - $r.Top
}
if ($w -le 0 -or $h -le 0) { Write-Output "BAD-RECT: ${w}x${h}"; exit 1 }

$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($x, $y, 0, 0, $bmp.Size)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "SAVED: $Out (${w}x${h}, $((Get-Item $Out).Length) bytes)"
