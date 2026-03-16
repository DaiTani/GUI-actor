import os
import sys
import time
import base64
import subprocess
import json
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "shared"))

from protocol import WindowInfo
from constants import DefaultConfig
from utils import setup_logger, get_project_root


SCRIPT_DIR = get_project_root()
WIN_TMP = SCRIPT_DIR / "tmp_Image"
WSL_TMP = "./tmp_Image"
SCREENSHOT_NAME = "current_screen.png"


class ScreenshotService:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = setup_logger('ScreenshotService', config.get('logging'))
        
        self.screenshot_width = config.get('screenshot', {}).get('width', DefaultConfig.SCREENSHOT_WIDTH)
        
        self.target_window: Optional[int] = None
        self.target_window_rect: Optional[Dict[str, int]] = None
        self.current_monitor_index: int = 0
        self.monitors_info: List[Dict[str, Any]] = []
        self.screen_width: int = 1920
        self.screen_height: int = 1080
        
        WIN_TMP.mkdir(parents=True, exist_ok=True)
        
        self._init_monitors()
    
    def _init_monitors(self):
        self.monitors_info = self.get_all_monitors()
        if self.monitors_info:
            primary_idx = 0
            for i, m in enumerate(self.monitors_info):
                if m.get('Primary'):
                    primary_idx = i
                    break
            
            self.current_monitor_index = primary_idx
            monitor = self.monitors_info[self.current_monitor_index]
            phys = monitor.get('Physical', '1920,1080').split(',')
            self.screen_width, self.screen_height = int(phys[0]), int(phys[1])
            self.logger.info(f"Primary monitor: {self.screen_width}x{self.screen_height}")
    
    def get_all_monitors(self) -> List[Dict[str, Any]]:
        ps_script = '''
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class MonitorInfo {
    [DllImport("user32.dll")] public static extern IntPtr GetDC(IntPtr hwnd);
    [DllImport("gdi32.dll")] public static extern int GetDeviceCaps(IntPtr hdc, int nIndex);
    [DllImport("user32.dll")] public static extern int ReleaseDC(IntPtr hwnd, IntPtr hdc);
    public const int DESKTOPHORZRES = 118;
    public const int DESKTOPVERTRES = 117;
    public const int HORZRES = 8;
    public const int VERTRES = 10;
}
'@
Add-Type -AssemblyName System.Windows.Forms
$monitors = @()
foreach ($screen in [System.Windows.Forms.Screen]::AllScreens) {
    $hdc = [MonitorInfo]::GetDC([IntPtr]::Zero)
    $physW = [MonitorInfo]::GetDeviceCaps($hdc, [MonitorInfo]::DESKTOPHORZRES)
    $physH = [MonitorInfo]::GetDeviceCaps($hdc, [MonitorInfo]::DESKTOPVERTRES)
    $logW = [MonitorInfo]::GetDeviceCaps($hdc, [MonitorInfo]::HORZRES)
    $logH = [MonitorInfo]::GetDeviceCaps($hdc, [MonitorInfo]::VERTRES)
    [MonitorInfo]::ReleaseDC([IntPtr]::Zero, $hdc) | Out-Null
    $dpiScale = [math]::Round($physW / $logW, 2)
    $monitors += @{
        Name = $screen.DeviceName
        Primary = $screen.Primary
        Bounds = "$($screen.Bounds.Width),$($screen.Bounds.Height)"
        Physical = "$physW,$physH"
        DpiScale = $dpiScale
        X = $screen.Bounds.X
        Y = $screen.Bounds.Y
    }
}
$monitors | ConvertTo-Json -Compress
'''
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True, timeout=30, encoding='utf-8', errors='ignore'
        )
        
        if result.stderr:
            self.logger.error(f"Monitor detection error: {result.stderr[:200]}")
        
        try:
            monitors = json.loads(result.stdout.strip())
            if not isinstance(monitors, list):
                monitors = [monitors]
            return monitors
        except Exception as e:
            self.logger.error(f"Parse monitor info error: {e}")
            return []
    
    def get_window_list(self) -> List[Dict[str, Any]]:
        ps_script = '''
Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Text;
[StructLayout(LayoutKind.Sequential)]
public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
public class WinAPI {
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
}
'@
$windows = @()
$callback = {
    param([IntPtr]$hwnd, [IntPtr]$lParam)
    if ([WinAPI]::IsWindowVisible($hwnd)) {
        $len = [WinAPI]::GetWindowTextLength($hwnd)
        if ($len -gt 0) {
            $sb = New-Object System.Text.StringBuilder($len + 1)
            [WinAPI]::GetWindowText($hwnd, $sb, $sb.Capacity) | Out-Null
            $title = $sb.ToString()
            if ($title -ne "") {
                $rect = New-Object RECT
                [WinAPI]::GetWindowRect($hwnd, [ref]$rect) | Out-Null
                $width = $rect.Right - $rect.Left
                $height = $rect.Bottom - $rect.Top
                if ($width -gt 100 -and $height -gt 100) {
                    $winPid = [uint32]0
                    [WinAPI]::GetWindowThreadProcessId($hwnd, [ref]$winPid) | Out-Null
                    $procName = "unknown"
                    try {
                        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$winPid" -ErrorAction SilentlyContinue
                        if ($proc) { $procName = $proc.Name -replace "\\.exe$", "" }
                    } catch {}
                    $script:windows += @{
                        Handle = $hwnd.ToInt64()
                        Title = $title
                        ProcessName = $procName
                        Width = $width
                        Height = $height
                        X = $rect.Left
                        Y = $rect.Top
                    }
                }
            }
        }
    }
    return $true
}
$delegate = [WinAPI+EnumWindowsProc]$callback
[WinAPI]::EnumWindows($delegate, [IntPtr]::Zero) | Out-Null
if ($windows.Count -eq 0) { "[]" } else { $windows | Sort-Object { $_.Title } | ConvertTo-Json -Compress }
'''
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True, timeout=30, encoding='utf-8', errors='ignore'
        )
        
        if result.stderr:
            self.logger.error(f"Window list error: {result.stderr[:200]}")
        
        output = result.stdout.strip()
        if not output:
            self.logger.warning("Window list returned empty output")
            return []
        
        try:
            windows = json.loads(output)
            if not isinstance(windows, list):
                windows = [windows]
            return windows
        except Exception as e:
            self.logger.error(f"Parse window list error: {e}")
            return []
    
    def set_target_window(self, window_handle: int) -> bool:
        ps_script = f'''
Add-Type @'
using System;
using System.Runtime.InteropServices;
[StructLayout(LayoutKind.Sequential)]
public struct RECT {{
    public int Left;
    public int Top;
    public int Right;
    public int Bottom;
}}
public class WinAPI {{
    [DllImport("user32.dll", SetLastError=true)] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll", SetLastError=true)] public static extern bool IsWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern int GetDpiForWindow(IntPtr hWnd);
}}
'@
[WinAPI]::SetProcessDPIAware() | Out-Null
$hwnd = [IntPtr]({window_handle})
$isValid = [WinAPI]::IsWindow($hwnd)
if ($isValid) {{
    $rect = New-Object RECT
    $result = [WinAPI]::GetWindowRect($hwnd, [ref]$rect)
    if ($result) {{
        $dpi = [WinAPI]::GetDpiForWindow($hwnd)
        if ($dpi -eq 0) {{ $dpi = 96 }}
        Write-Output "$($rect.Left),$($rect.Top),$($rect.Right),$($rect.Bottom),$dpi"
    }} else {{
        Write-Output "RECT_FAILED"
    }}
}} else {{
    Write-Output "INVALID"
}}
'''
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True, timeout=10, encoding='utf-8', errors='ignore'
        )
        
        output = result.stdout.strip()
        
        if output == "INVALID" or output == "RECT_FAILED" or not output:
            self.logger.error(f"Window handle validation failed: handle={window_handle}")
            return False
        
        try:
            parts = output.split(",")
            if len(parts) >= 5:
                self.target_window = window_handle
                self.target_window_rect = {
                    'left': int(parts[0]),
                    'top': int(parts[1]),
                    'right': int(parts[2]),
                    'bottom': int(parts[3]),
                    'dpi': int(parts[4])
                }
                self.screen_width = self.target_window_rect['right'] - self.target_window_rect['left']
                self.screen_height = self.target_window_rect['bottom'] - self.target_window_rect['top']
                self.logger.info(f"Target window set: handle={window_handle}, rect={self.target_window_rect}")
                return True
        except Exception as e:
            self.logger.error(f"Parse window rect error: {e}")
        
        return False
    
    def clear_target_window(self):
        self.target_window = None
        self.target_window_rect = None
        self._init_monitors()
        self.logger.info("Target window cleared")
    
    def capture_screen(self) -> Optional[Tuple[bytes, WindowInfo]]:
        win_img = str(WIN_TMP / SCREENSHOT_NAME)
        wsl_img = WSL_TMP + "/" + SCREENSHOT_NAME
        
        if self.target_window:
            return self._capture_window(win_img, wsl_img)
        else:
            return self._capture_monitor(win_img, wsl_img)
    
    def _capture_window(self, win_img: str, wsl_img: str) -> Optional[Tuple[bytes, WindowInfo]]:
        if not self.target_window_rect:
            self.logger.error("No target window rect")
            return None
        
        refresh_rect_script = f'''
Add-Type @'
using System;
using System.Runtime.InteropServices;
[StructLayout(LayoutKind.Sequential)]
public struct RECT {{
    public int Left;
    public int Top;
    public int Right;
    public int Bottom;
}}
public class WinAPI {{
    [DllImport("user32.dll", SetLastError=true)] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll", SetLastError=true)] public static extern bool IsWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}}
'@
[WinAPI]::SetProcessDPIAware() | Out-Null
$hwnd = [IntPtr]({self.target_window})
if ([WinAPI]::IsWindow($hwnd)) {{
    $rect = New-Object RECT
    [WinAPI]::GetWindowRect($hwnd, [ref]$rect) | Out-Null
    Write-Output "$($rect.Left),$($rect.Top),$($rect.Right),$($rect.Bottom)"
}} else {{
    Write-Output "INVALID"
}}
'''
        rect_result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", refresh_rect_script],
            capture_output=True, timeout=10, encoding='utf-8', errors='ignore'
        )
        rect_output = rect_result.stdout.strip()
        if rect_output and rect_output != "INVALID":
            parts = rect_output.split(",")
            if len(parts) >= 4:
                self.target_window_rect = {
                    'left': int(parts[0]),
                    'top': int(parts[1]),
                    'right': int(parts[2]),
                    'bottom': int(parts[3]),
                    'dpi': self.target_window_rect.get('dpi', 96)
                }
        
        left = self.target_window_rect['left']
        top = self.target_window_rect['top']
        right = self.target_window_rect['right']
        bottom = self.target_window_rect['bottom']
        width = right - left
        height = bottom - top
        dpi = self.target_window_rect.get('dpi', 96)
        
        ps_script = f'''
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class WinCapture {{
    [DllImport("user32.dll")] public static extern IntPtr GetWindowDC(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern IntPtr GetDC(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern int ReleaseDC(IntPtr hwnd, IntPtr hdc);
    [DllImport("gdi32.dll")] public static extern IntPtr CreateCompatibleDC(IntPtr hdc);
    [DllImport("gdi32.dll")] public static extern IntPtr CreateCompatibleBitmap(IntPtr hdc, int nWidth, int nHeight);
    [DllImport("gdi32.dll")] public static extern IntPtr SelectObject(IntPtr hdc, IntPtr hgdiobj);
    [DllImport("gdi32.dll")] public static extern int BitBlt(IntPtr hdcDest, int nXDest, int nYDest, int nWidth, int nHeight, IntPtr hdcSrc, int nXSrc, int nYSrc, int dwRop);
    [DllImport("gdi32.dll")] public static extern int DeleteDC(IntPtr hdc);
    [DllImport("gdi32.dll")] public static extern int DeleteObject(IntPtr hObject);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hwnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    public const int SRCCOPY = 0x00CC0020;
    public const int SW_RESTORE = 9;
}}
'@
[WinCapture]::SetProcessDPIAware() | Out-Null
$hwnd = [IntPtr]({self.target_window})
if ([WinCapture]::IsIconic($hwnd)) {{
    [WinCapture]::ShowWindow($hwnd, [WinCapture]::SW_RESTORE) | Out-Null
    Start-Sleep -Milliseconds 200
}}
[WinCapture]::SetForegroundWindow($hwnd) | Out-Null
Start-Sleep -Milliseconds 100
$screenDC = [WinCapture]::GetDC([IntPtr]::Zero)
$memDC = [WinCapture]::CreateCompatibleDC($screenDC)
$bmp = [WinCapture]::CreateCompatibleBitmap($screenDC, {width}, {height})
[WinCapture]::SelectObject($memDC, $bmp) | Out-Null
[WinCapture]::BitBlt($memDC, 0, 0, {width}, {height}, $screenDC, {left}, {top}, [WinCapture]::SRCCOPY) | Out-Null
$img = [System.Drawing.Image]::FromHbitmap($bmp)
$newW = {self.screenshot_width}
$newH = [int]({height} * {self.screenshot_width} / {width})
$resized = New-Object System.Drawing.Bitmap($newW, $newH)
$g = [System.Drawing.Graphics]::FromImage($resized)
$g.DrawImage($img, 0, 0, $newW, $newH)
$resized.Save('{win_img}')
$g.Dispose(); $img.Dispose(); $resized.Dispose()
[WinCapture]::DeleteObject($bmp) | Out-Null
[WinCapture]::DeleteDC($memDC) | Out-Null
[WinCapture]::ReleaseDC([IntPtr]::Zero, $screenDC) | Out-Null
Write-Output "$newW,$newH,{width},{height}"
'''
        start = time.time()
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True, timeout=60, encoding='utf-8', errors='ignore'
        )
        elapsed = time.time() - start
        self.logger.info(f"Window screenshot took {elapsed:.2f}s")
        
        if result.stderr:
            self.logger.error(f"PowerShell error: {result.stderr[:500]}")
        
        if not os.path.exists(wsl_img):
            self.logger.error(f"Screenshot not found at {wsl_img}")
            return None
        
        try:
            last_line = result.stdout.strip().split("\n")[-1]
            parts = last_line.split(",")
            if len(parts) >= 4:
                img_w, img_h = int(parts[0]), int(parts[1])
                orig_w, orig_h = int(parts[2]), int(parts[3])
                
                with open(wsl_img, 'rb') as f:
                    image_data = f.read()
                
                window_info = WindowInfo(
                    width=img_w,
                    height=img_h,
                    orig_width=orig_w,
                    orig_height=orig_h,
                    dpi=dpi,
                    dpi_scale=dpi / 96.0,
                    offset_x=left,
                    offset_y=top,
                    target_window_handle=self.target_window,
                    target_window_rect=self.target_window_rect
                )
                
                return image_data, window_info
        except Exception as e:
            self.logger.error(f"Parse error: {e}")
        
        return None
    
    def _capture_monitor(self, win_img: str, wsl_img: str) -> Optional[Tuple[bytes, WindowInfo]]:
        if not self.monitors_info:
            self.monitors_info = self.get_all_monitors()
        
        if not self.monitors_info:
            self.logger.error("No monitors detected")
            return None
        
        monitor = self.monitors_info[self.current_monitor_index] if self.current_monitor_index < len(self.monitors_info) else self.monitors_info[0]
        phys_str = monitor.get('Physical', '1920,1080')
        phys_parts = phys_str.split(',')
        phys_w, phys_h = int(phys_parts[0]), int(phys_parts[1])
        
        ps_script = f'''
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class Capture {{
    [DllImport("user32.dll")] public static extern IntPtr GetDC(IntPtr hwnd);
    [DllImport("gdi32.dll")] public static extern IntPtr CreateCompatibleDC(IntPtr hdc);
    [DllImport("gdi32.dll")] public static extern IntPtr CreateCompatibleBitmap(IntPtr hdc, int nWidth, int nHeight);
    [DllImport("gdi32.dll")] public static extern IntPtr SelectObject(IntPtr hdc, IntPtr hgdiobj);
    [DllImport("gdi32.dll")] public static extern int BitBlt(IntPtr hdcDest, int nXDest, int nYDest, int nWidth, int nHeight, IntPtr hdcSrc, int nXSrc, int nYSrc, int dwRop);
    [DllImport("gdi32.dll")] public static extern int DeleteDC(IntPtr hdc);
    [DllImport("gdi32.dll")] public static extern int DeleteObject(IntPtr hObject);
    [DllImport("user32.dll")] public static extern int ReleaseDC(IntPtr hwnd, IntPtr hdc);
    public const int SRCCOPY = 0x00CC0020;
}}
'@
$hdc = [Capture]::GetDC([IntPtr]::Zero)
$memDC = [Capture]::CreateCompatibleDC($hdc)
$bmp = [Capture]::CreateCompatibleBitmap($hdc, {phys_w}, {phys_h})
[Capture]::SelectObject($memDC, $bmp) | Out-Null
[Capture]::BitBlt($memDC, 0, 0, {phys_w}, {phys_h}, $hdc, 0, 0, [Capture]::SRCCOPY) | Out-Null
$img = [System.Drawing.Image]::FromHbitmap($bmp)
$newW = {self.screenshot_width}
$newH = [int]({phys_h} * {self.screenshot_width} / {phys_w})
$resized = New-Object System.Drawing.Bitmap($newW, $newH)
$g = [System.Drawing.Graphics]::FromImage($resized)
$g.DrawImage($img, 0, 0, $newW, $newH)
$resized.Save('{win_img}')
$g.Dispose(); $img.Dispose(); $resized.Dispose()
[Capture]::DeleteObject($bmp) | Out-Null
[Capture]::DeleteDC($memDC) | Out-Null
[Capture]::ReleaseDC([IntPtr]::Zero, $hdc) | Out-Null
Write-Output "$newW,$newH,{phys_w},{phys_h}"
'''
        
        start = time.time()
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True, timeout=60, encoding='utf-8', errors='ignore'
        )
        elapsed = time.time() - start
        self.logger.info(f"Screenshot took {elapsed:.2f}s")
        
        if result.stderr:
            self.logger.error(f"PowerShell error: {result.stderr[:500]}")
        
        if not os.path.exists(wsl_img):
            self.logger.error(f"Screenshot not found at {wsl_img}")
            return None
        
        try:
            last_line = result.stdout.strip().split("\n")[-1]
            parts = last_line.split(",")
            if len(parts) >= 4:
                img_w, img_h = int(parts[0]), int(parts[1])
                orig_w, orig_h = int(parts[2]), int(parts[3])
                
                with open(wsl_img, 'rb') as f:
                    image_data = f.read()
                
                dpi_scale = monitor.get('DpiScale', 1.0)
                
                window_info = WindowInfo(
                    width=img_w,
                    height=img_h,
                    orig_width=orig_w,
                    orig_height=orig_h,
                    dpi=int(96 * dpi_scale),
                    dpi_scale=dpi_scale,
                    offset_x=monitor.get('X', 0),
                    offset_y=monitor.get('Y', 0)
                )
                
                return image_data, window_info
        except Exception as e:
            self.logger.error(f"Parse error: {e}")
        
        return None
    
    def get_screen_info(self) -> Dict[str, Any]:
        return {
            'width': self.screen_width,
            'height': self.screen_height,
            'monitor_index': self.current_monitor_index,
            'target_window': self.target_window,
            'target_window_rect': self.target_window_rect
        }
