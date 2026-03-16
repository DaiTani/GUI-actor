import os
import sys
import torch
import subprocess
import json
import time
import base64
import logging
import threading
import socket
import cv2
import numpy as np
import pyautogui
import yaml
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Optional, Dict, Any, List, Tuple

SCRIPT_DIR = Path(__file__).parent.resolve()
GUI_ACTOR_DIR = SCRIPT_DIR / "GUI-Actor"
sys.path.insert(0, str(GUI_ACTOR_DIR / "src"))
sys.path.insert(0, str(SCRIPT_DIR / "OmniParser"))

from gui_actor.modeling import Qwen2VLForConditionalGenerationWithPointer
from gui_actor.inference import inference
from gui_actor.constants import grounding_system_message
from transformers import Qwen2VLProcessor
from util.omniparser import Omniparser

CONFIG = None
WIN_TMP = SCRIPT_DIR / "tmp_Image"
WSL_TMP = "./tmp_Image"
SCREENSHOT_NAME = "current_screen.png"
DEBUG_NAME = "debug_target.png"

def load_config() -> dict:
    config_path = SCRIPT_DIR / "config.yaml"
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}

CONFIG = load_config()

LOG_FILE = SCRIPT_DIR / CONFIG.get('logging', {}).get('file', 'ferment_control.log')
SCREENSHOT_WIDTH = CONFIG.get('screenshot', {}).get('width', 1280)

OMNIPARSER_WEIGHTS = SCRIPT_DIR / "OmniParser" / "weights"
OMNIPARSER_CONFIG = {
    'som_model_path': str(OMNIPARSER_WEIGHTS / "icon_detect" / "model.pt"),
    'caption_model_name': 'florence2',
    'caption_model_path': str(OMNIPARSER_WEIGHTS / "icon_caption_florence"),
    'BOX_TRESHOLD': CONFIG.get('model', {}).get('box_threshold', 0.05)
}

logging.basicConfig(
    level=getattr(logging, CONFIG.get('logging', {}).get('level', 'INFO')),
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

processor = None
gui_model = None
omni_parser = None
screen_width = None
screen_height = None
current_monitor_index = 0
monitors_info = []
target_window = None
target_window_rect = None
debug_steps = []

class OperationResult:
    SUCCESS = "success"
    FAILED = "failed"
    RETRY = "retry"
    TIMEOUT = "timeout"

class UIElement:
    def __init__(self, elem_type: str, text: str, bbox: List[float], interactivity: bool, confidence: float = 1.0):
        self.elem_type = elem_type
        self.text = text
        self.bbox = bbox
        self.interactivity = interactivity
        self.confidence = confidence
    
    def get_center(self) -> Tuple[int, int]:
        if self.bbox and len(self.bbox) >= 4:
            x = int((self.bbox[0] + self.bbox[2]) / 2 * screen_width)
            y = int((self.bbox[1] + self.bbox[3]) / 2 * screen_height)
            return (x, y)
        return (0, 0)

def select_target_window():
    global target_window, target_window_rect, screen_width, screen_height
    
    logger.info("Fetching window list...")
    windows = get_window_list()
    
    if not windows:
        logger.warning("No windows found")
        return False
    
    print("\n" + "=" * 60)
    print("Available Windows:")
    print("-" * 60)
    for i, win in enumerate(windows[:30]):
        title = win.get('Title', 'Unknown')[:40]
        proc = win.get('ProcessName', 'Unknown')[:15]
        w, h = win.get('Width', 0), win.get('Height', 0)
        print(f"  {i:2d}. [{proc:15s}] {title} ({w}x{h})")
    
    if len(windows) > 30:
        print(f"  ... and {len(windows) - 30} more windows")
    print("-" * 60)
    print("  'c' - Clear target window (use full screen)")
    print("  'q' - Skip window selection")
    print("=" * 60)
    
    try:
        choice = input("Select target window (number/c/q): ").strip().lower()
        
        if choice == 'q':
            logger.info("Window selection skipped")
            return False
        
        if choice == 'c':
            clear_target_window()
            monitor = monitors_info[current_monitor_index] if current_monitor_index < len(monitors_info) else monitors_info[0]
            phys = monitor.get('Physical', '1920,1080').split(',')
            screen_width, screen_height = int(phys[0]), int(phys[1])
            logger.info(f"Cleared target window, using full screen: {screen_width}x{screen_height}")
            return True
        
        idx = int(choice)
        if 0 <= idx < len(windows):
            win = windows[idx]
            handle = win.get('Handle')
            if set_target_window(handle):
                screen_width = win.get('Width', screen_width)
                screen_height = win.get('Height', screen_height)
                print(f"\nTarget window set: {win.get('Title', 'Unknown')}")
                print(f"Window size: {screen_width}x{screen_height}")
                print(f"Window position: ({win.get('X', 0)}, {win.get('Y', 0)})")
                return True
            else:
                logger.error("Failed to set target window")
                return False
        else:
            logger.warning(f"Invalid selection: {idx}")
            return False
    except ValueError:
        logger.warning("Invalid input")
        return False
    except Exception as e:
        logger.error(f"Window selection error: {e}")
        return False

def setup_models(server_mode: bool = False):
    global processor, gui_model, omni_parser, screen_width, screen_height, current_monitor_index, monitors_info
    if processor is not None:
        return
    
    logger.info("Loading GUI-Actor model...")
    processor = Qwen2VLProcessor.from_pretrained("microsoft/GUI-Actor-2B-Qwen2-VL")
    gui_model = Qwen2VLForConditionalGenerationWithPointer.from_pretrained(
        "microsoft/GUI-Actor-2B-Qwen2-VL", torch_dtype=torch.bfloat16, device_map="cuda",
        trust_remote_code=True, attn_implementation="sdpa"
    ).eval()
    
    logger.info("Loading OmniParser model...")
    omni_parser = Omniparser(OMNIPARSER_CONFIG)
    
    monitors_info = get_all_monitors()
    if monitors_info:
        print("\n" + "=" * 50)
        print("Detected monitors:")
        for i, m in enumerate(monitors_info):
            primary = " [PRIMARY]" if m.get('Primary') else ""
            phys = m.get('Physical', 'unknown')
            dpi = m.get('DpiScale', 1.0)
            print(f"  {i}: {m.get('Name', 'Unknown')}{primary}")
            print(f"     Physical: {phys}, DPI Scale: {dpi}")
        print("=" * 50)
        
        primary_idx = 0
        for i, m in enumerate(monitors_info):
            if m.get('Primary'):
                primary_idx = i
                break
        
        if server_mode:
            current_monitor_index = primary_idx
            logger.info(f"Server mode: auto-selected primary monitor {primary_idx}")
        elif len(monitors_info) > 1:
            try:
                choice = input(f"Select monitor (0-{len(monitors_info)-1}, default={primary_idx}): ").strip()
                current_monitor_index = int(choice) if choice else primary_idx
            except:
                current_monitor_index = primary_idx
        else:
            current_monitor_index = 0
        
        monitor = monitors_info[current_monitor_index]
        phys = monitor.get('Physical', '1920,1080').split(',')
        screen_width, screen_height = int(phys[0]), int(phys[1])
        logger.info(f"Selected monitor {current_monitor_index}: {screen_width}x{screen_height}")
    else:
        screen_width, screen_height = 1920, 1080
        logger.warning("No monitors detected, using default 1920x1080")
    
    if not server_mode:
        select_target_window()
    
    logger.info("Warming up model...")
    _ = locate_target("test")
    logger.info("Model ready!")

def get_all_monitors() -> List[Dict[str, Any]]:
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
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script], 
                          capture_output=True, timeout=30, encoding='utf-8', errors='ignore')
    
    if result.stderr:
        logger.error(f"Monitor detection error: {result.stderr[:200]}")
    
    try:
        monitors = json.loads(result.stdout.strip())
        if not isinstance(monitors, list):
            monitors = [monitors]
        return monitors
    except Exception as e:
        logger.error(f"Parse monitor info error: {e}")
        return []

def get_primary_monitor() -> Dict[str, Any]:
    monitors = get_all_monitors()
    for m in monitors:
        if m.get('Primary', False):
            return m
    return monitors[0] if monitors else {}

def get_window_list() -> List[Dict[str, Any]]:
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
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script], 
                          capture_output=True, timeout=30, encoding='utf-8', errors='ignore')
    
    if result.stderr:
        logger.error(f"Window list error: {result.stderr[:200]}")
    
    output = result.stdout.strip()
    if not output:
        logger.warning("Window list returned empty output")
        return []
    
    try:
        windows = json.loads(output)
        if not isinstance(windows, list):
            windows = [windows]
        return windows
    except Exception as e:
        logger.error(f"Parse window list error: {e}, output: {output[:100] if output else 'empty'}")
        return []

def set_target_window(window_handle: int) -> bool:
    global target_window, target_window_rect
    
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
    [DllImport("shcore.dll")] public static extern int GetDpiForMonitor(IntPtr hmonitor, int dpiType, out uint dpiX, out uint dpiY);
    [DllImport("user32.dll")] public static extern IntPtr MonitorFromWindow(IntPtr hwnd, uint dwFlags);
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
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script], 
                          capture_output=True, timeout=10, encoding='utf-8', errors='ignore')
    
    output = result.stdout.strip()
    stderr = result.stderr.strip() if result.stderr else ""
    
    if output == "INVALID" or output == "RECT_FAILED" or not output:
        logger.error(f"Window handle validation failed: handle={window_handle}, output={output}, stderr={stderr[:100] if stderr else 'none'}")
        return False
    
    try:
        parts = output.split(",")
        if len(parts) >= 5:
            target_window = window_handle
            target_window_rect = {
                'left': int(parts[0]),
                'top': int(parts[1]),
                'right': int(parts[2]),
                'bottom': int(parts[3]),
                'dpi': int(parts[4])
            }
            logger.info(f"Target window set: handle={window_handle}, rect={target_window_rect}")
            return True
    except Exception as e:
        logger.error(f"Parse window rect error: {e}, output={output}")
    
    return False

def clear_target_window():
    global target_window, target_window_rect
    target_window = None
    target_window_rect = None
    logger.info("Target window cleared")

def capture_screen() -> Optional[Tuple[str, int, int, int, int]]:
    global current_monitor_index, monitors_info, target_window, target_window_rect
    
    WIN_TMP.mkdir(parents=True, exist_ok=True)
    win_img = str(WIN_TMP / SCREENSHOT_NAME)
    wsl_img = WSL_TMP + "/" + SCREENSHOT_NAME
    
    if target_window:
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
$hwnd = [IntPtr]({target_window})
if ([WinAPI]::IsWindow($hwnd)) {{
    $rect = New-Object RECT
    [WinAPI]::GetWindowRect($hwnd, [ref]$rect) | Out-Null
    Write-Output "$($rect.Left),$($rect.Top),$($rect.Right),$($rect.Bottom)"
}} else {{
    Write-Output "INVALID"
}}
'''
        rect_result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", refresh_rect_script], 
                                    capture_output=True, timeout=10, encoding='utf-8', errors='ignore')
        rect_output = rect_result.stdout.strip()
        if rect_output and rect_output != "INVALID":
            parts = rect_output.split(",")
            if len(parts) >= 4:
                target_window_rect = {
                    'left': int(parts[0]),
                    'top': int(parts[1]),
                    'right': int(parts[2]),
                    'bottom': int(parts[3]),
                    'dpi': target_window_rect.get('dpi', 96) if target_window_rect else 96
                }
                logger.info(f"Refreshed window rect: {target_window_rect}")
        
        if not target_window_rect:
            logger.error("Failed to get window rect")
            return None
        
        left = target_window_rect['left']
        top = target_window_rect['top']
        right = target_window_rect['right']
        bottom = target_window_rect['bottom']
        width = right - left
        height = bottom - top
        dpi = target_window_rect.get('dpi', 96)
        dpi_scale = dpi / 96.0
        
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
$hwnd = [IntPtr]({target_window})
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
$newW = {SCREENSHOT_WIDTH}
$newH = [int]({height} * {SCREENSHOT_WIDTH} / {width})
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
        result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script], 
                              capture_output=True, timeout=60, encoding='utf-8', errors='ignore')
        elapsed = time.time() - start
        logger.info(f"Window screenshot took {elapsed:.2f}s")
        
        if result.stderr:
            logger.error(f"PowerShell error: {result.stderr[:500]}")
        
        if not os.path.exists(wsl_img):
            logger.error(f"Screenshot not found at {wsl_img}")
            return None
        
        try:
            last_line = result.stdout.strip().split("\n")[-1]
            parts = last_line.split(",")
            if len(parts) >= 4:
                return wsl_img, int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
        except Exception as e:
            logger.error(f"Parse error: {e}, output: {result.stdout}")
        
        return None
    
    if not monitors_info:
        monitors_info = get_all_monitors()
    
    if not monitors_info:
        logger.error("No monitors detected")
        return None
    
    monitor = monitors_info[current_monitor_index] if current_monitor_index < len(monitors_info) else monitors_info[0]
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
$newW = {SCREENSHOT_WIDTH}
$newH = [int]({phys_h} * {SCREENSHOT_WIDTH} / {phys_w})
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
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script], 
                          capture_output=True, timeout=60, encoding='utf-8', errors='ignore')
    elapsed = time.time() - start
    logger.info(f"Screenshot took {elapsed:.2f}s")
    
    if result.stderr:
        logger.error(f"PowerShell error: {result.stderr[:500]}")
    
    if not os.path.exists(wsl_img):
        logger.error(f"Screenshot not found at {wsl_img}")
        return None
    
    try:
        last_line = result.stdout.strip().split("\n")[-1]
        parts = last_line.split(",")
        if len(parts) >= 4:
            return wsl_img, int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
    except Exception as e:
        logger.error(f"Parse error: {e}, output: {result.stdout}")
    
    return None

def get_screen_size() -> Tuple[int, int]:
    global screen_width, screen_height
    if screen_width and screen_height:
        return screen_width, screen_height
    
    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
        "Write-Output \"$($bounds.Width),$($bounds.Height)\""
    )
    result = subprocess.run(["powershell.exe", "-Command", ps_script], capture_output=True, text=True)
    parts = result.stdout.strip().split(",")
    screen_width, screen_height = int(parts[0]), int(parts[1])
    logger.info(f"Screen resolution: {screen_width}x{screen_height}")
    return screen_width, screen_height

def parse_ui_elements(image_path: str) -> List[UIElement]:
    with open(image_path, "rb") as f:
        image_base64 = base64.b64encode(f.read()).decode()
    
    _, parsed_content_list = omni_parser.parse(image_base64)
    
    elements = []
    for item in parsed_content_list:
        if isinstance(item, dict):
            elem = UIElement(
                elem_type=item.get('type', 'unknown'),
                text=item.get('content', ''),
                bbox=item.get('bbox', None),
                interactivity=item.get('interactivity', False),
                confidence=item.get('confidence', 1.0)
            )
            elements.append(elem)
    return elements

def find_element_by_text(elements: List[UIElement], target_text: str, fuzzy: bool = True) -> Optional[UIElement]:
    target_lower = target_text.lower()
    keywords = [w for w in target_lower.split() if len(w) > 2 and w not in ['the', 'for', 'and', 'not', 'are', 'you', 'area', 'page', 'very', 'middle']]
    
    input_keywords = ['search', 'input', 'text', 'edit', 'box', 'field', 'type', 'enter', 'write', '输入', '文本', '搜索']
    button_keywords = ['button', 'click', 'submit', 'send', 'confirm', 'ok', 'cancel', '按钮', '点击', '确定', '取消', '开始', '启动', '提交']
    
    logger.info(f"Finding element for target: '{target_text}'")
    logger.info(f"Keywords extracted: {keywords}")
    logger.info(f"Detected {len(elements)} elements from OmniParser")
    for i, elem in enumerate(elements[:20]):
        logger.info(f"  [{i}] text='{elem.text}', interactive={elem.interactivity}, type={elem.elem_type}")
    
    best_match = None
    best_score = 0
    
    for elem in elements:
        elem_text_lower = elem.text.lower()
        score = 0
        
        if elem.interactivity:
            score += 15
        
        if fuzzy:
            if target_lower in elem_text_lower or elem_text_lower in target_lower:
                score += 100
            else:
                for kw in keywords:
                    if kw in elem_text_lower:
                        score += 10
                for word in elem_text_lower.split():
                    if word in target_lower and len(word) > 2:
                        score += 5
                
                for ikw in input_keywords:
                    if ikw in target_lower and ikw in elem_text_lower:
                        score += 20
                for bkw in button_keywords:
                    if bkw in target_lower and bkw in elem_text_lower:
                        score += 20
                
                for kw in keywords:
                    if kw in elem_text_lower or elem_text_lower in kw:
                        score += 15
        else:
            if target_lower == elem_text_lower:
                score = 100
        
        if score > best_score:
            best_score = score
            best_match = elem
    
    if best_match and best_score >= 10:
        logger.info(f"Matched element '{best_match.text}' (interactive={best_match.interactivity}) with score {best_score}")
        return best_match
    logger.warning(f"No element matched with score >= 10, best was '{best_match.text}' with score {best_score}" if best_match else "No elements found")
    return None

def find_element_by_text_with_score(elements: List[UIElement], target_text: str, fuzzy: bool = True) -> Tuple[Optional[UIElement], int]:
    target_lower = target_text.lower()
    keywords = [w for w in target_lower.split() if len(w) > 2 and w not in ['the', 'for', 'and', 'not', 'are', 'you', 'area', 'page', 'very', 'middle']]
    
    input_keywords = ['search', 'input', 'text', 'edit', 'box', 'field', 'type', 'enter', 'write', '输入', '文本', '搜索']
    button_keywords = ['button', 'click', 'submit', 'send', 'confirm', 'ok', 'cancel', '按钮', '点击', '确定', '取消', '开始', '启动', '提交']
    
    best_match = None
    best_score = 0
    
    for elem in elements:
        elem_text_lower = elem.text.lower()
        score = 0
        
        if elem.interactivity:
            score += 15
        
        if fuzzy:
            if target_lower in elem_text_lower or elem_text_lower in target_lower:
                score += 100
            else:
                for kw in keywords:
                    if kw in elem_text_lower:
                        score += 10
                for word in elem_text_lower.split():
                    if word in target_lower and len(word) > 2:
                        score += 5
                
                for ikw in input_keywords:
                    if ikw in target_lower and ikw in elem_text_lower:
                        score += 20
                for bkw in button_keywords:
                    if bkw in target_lower and bkw in elem_text_lower:
                        score += 20
                
                for kw in keywords:
                    if kw in elem_text_lower or elem_text_lower in kw:
                        score += 15
        else:
            if target_lower == elem_text_lower:
                score = 100
        
        if score > best_score:
            best_score = score
            best_match = elem
    
    return best_match, best_score

def find_element_by_type(elements: List[UIElement], target_type: str) -> List[UIElement]:
    target_lower = target_type.lower()
    return [e for e in elements if target_lower in e.type.lower()]

def locate_target(prompt: str, step_info: str = "") -> Optional[Tuple[int, int]]:
    global debug_steps
    logger.info(f"Capturing screen for target: {prompt}")
    
    t0 = time.time()
    result = capture_screen()
    t1 = time.time()
    logger.info(f"Screenshot: {t1-t0:.2f}s")
    
    if not result:
        logger.error("Screenshot failed")
        return None
    
    img_path, img_w, img_h, orig_w, orig_h = result
    logger.info(f"Image: {img_w}x{img_h}, Original: {orig_w}x{orig_h}")
    
    img = cv2.imread(img_path)
    if img is None:
        logger.error(f"Failed to read image: {img_path}")
        os.remove(img_path)
        return None
    
    crop_region = None
    omni_score = 0
    if omni_parser and CONFIG.get('model', {}).get('enable_omniparser', True):
        logger.info("Step 1: OmniParser detecting UI elements...")
        elements = parse_ui_elements(img_path)
        matched_elem, omni_score = find_element_by_text_with_score(elements, prompt)
        if matched_elem and matched_elem.bbox and omni_score >= 30:
            bbox = matched_elem.bbox
            crop_x1 = int(bbox[0] * img_w)
            crop_y1 = int(bbox[1] * img_h)
            crop_x2 = int(bbox[2] * img_w)
            crop_y2 = int(bbox[3] * img_h)
            margin = 20
            crop_x1 = max(0, crop_x1 - margin)
            crop_y1 = max(0, crop_y1 - margin)
            crop_x2 = min(img_w, crop_x2 + margin)
            crop_y2 = min(img_h, crop_y2 + margin)
            crop_region = (crop_x1, crop_y1, crop_x2, crop_y2)
            logger.info(f"OmniParser found: '{matched_elem.text}' at bbox {bbox}, crop region: {crop_region}, score={omni_score}")
        else:
            logger.info(f"OmniParser: No good match found (best score={omni_score}), using full image for GUI-Actor")
    
    if crop_region:
        crop_img = img[crop_region[1]:crop_region[3], crop_region[0]:crop_region[2]]
        crop_path = img_path.replace(".png", "_crop.png")
        cv2.imwrite(crop_path, crop_img)
        inference_img_path = crop_path
        crop_w = crop_region[2] - crop_region[0]
        crop_h = crop_region[3] - crop_region[1]
    else:
        inference_img_path = img_path
        crop_w = img_w
        crop_h = img_h
    
    conversation = [
        {"role": "system", "content": [{"type": "text", "text": grounding_system_message}]},
        {"role": "user", "content": [
            {"type": "image", "image": f"file://{inference_img_path}"},
            {"type": "text", "text": prompt}
        ]}
    ]
    
    t2 = time.time()
    logger.info("Step 2: GUI-Actor precise localization...")
    pred = inference(
        conversation=conversation, model=gui_model, tokenizer=processor.tokenizer,
        data_processor=processor, use_placeholder=True, topk=5
    )
    t3 = time.time()
    logger.info(f"GUI-Actor inference: {t3-t2:.2f}s")
    
    if crop_region and inference_img_path != img_path:
        os.remove(inference_img_path)
    os.remove(img_path)
    
    if pred["topk_points"] and len(pred["topk_points"]) > 0:
        point = pred["topk_points"][0]
        confidence = pred["topk_values"][0] if pred["topk_values"] else None
        
        if crop_region:
            px_on_img = int(point[0] * crop_w) + crop_region[0]
            py_on_img = int(point[1] * crop_h) + crop_region[1]
        else:
            px_on_img = int(point[0] * img_w)
            py_on_img = int(point[1] * img_h)
        
        if target_window and target_window_rect:
            offset_x = target_window_rect['left']
            offset_y = target_window_rect['top']
            dpi_scale = target_window_rect.get('dpi', 96) / 96.0
        else:
            monitor = monitors_info[current_monitor_index] if current_monitor_index < len(monitors_info) else monitors_info[0]
            offset_x = monitor.get('X', 0)
            offset_y = monitor.get('Y', 0)
            dpi_scale = monitor.get('DpiScale', 1.0)
        
        real_x = int((px_on_img / img_w) * orig_w) + offset_x
        real_y = int((py_on_img / img_h) * orig_h) + offset_y
        
        logical_x = int(real_x / dpi_scale)
        logical_y = int(real_y / dpi_scale)
        
        debug_steps.append({
            'px': px_on_img,
            'py': py_on_img,
            'real_x': real_x,
            'real_y': real_y,
            'logical_x': logical_x,
            'logical_y': logical_y,
            'info': step_info or prompt[:30]
        })
        
        cv2.circle(img, (px_on_img, py_on_img), 30, (0, 0, 255), 3)
        cv2.putText(img, f"({real_x},{real_y})", (px_on_img + 35, py_on_img + 5), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        if crop_region:
            cv2.rectangle(img, (crop_region[0], crop_region[1]), (crop_region[2], crop_region[3]), (0, 255, 0), 2)
        
        debug_path = WSL_TMP + "/" + DEBUG_NAME
        cv2.imwrite(debug_path, img)
        
        logger.info(f"GUI-Actor normalized: ({point[0]:.4f}, {point[1]:.4f})")
        logger.info(f"On scaled image: ({px_on_img}, {py_on_img})")
        logger.info(f"DPI scale: {dpi_scale}")
        logger.info(f"Physical coords: ({real_x}, {real_y})")
        logger.info(f"Logical coords: ({logical_x}, {logical_y})")
        logger.info(f"Offset: ({offset_x}, {offset_y})")
        logger.info(f"Total: {time.time()-t0:.2f}s")
        
        return (logical_x, logical_y)
    
    logger.warning("Target not found")
    return None

def draw_all_debug_steps():
    global debug_steps
    if not debug_steps:
        return
    
    debug_path = WSL_TMP + "/" + DEBUG_NAME
    if not Path(debug_path).exists():
        return
    
    img = cv2.imread(debug_path)
    if img is None:
        return
    
    colors = [
        (0, 0, 255),
        (0, 255, 0),
        (255, 0, 0),
        (0, 255, 255),
        (255, 0, 255),
        (255, 255, 0),
        (128, 0, 255),
        (0, 128, 255),
    ]
    
    for i, step in enumerate(debug_steps):
        color = colors[i % len(colors)]
        px, py = step['px'], step['py']
        cv2.circle(img, (px, py), 30, color, 3)
        cv2.putText(img, f"{i+1}:({step['real_x']},{step['real_y']})", 
                   (px + 35, py + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    
    cv2.imwrite(debug_path, img)
    logger.info(f"Debug image updated with {len(debug_steps)} steps")

def clear_debug_steps():
    global debug_steps
    debug_steps = []

def is_within_target_window(x: int, y: int) -> bool:
    if not target_window or not target_window_rect:
        return True
    dpi_scale = target_window_rect.get('dpi', 96) / 96.0
    physical_x = int(x * dpi_scale)
    physical_y = int(y * dpi_scale)
    return (target_window_rect['left'] <= physical_x <= target_window_rect['right'] and
            target_window_rect['top'] <= physical_y <= target_window_rect['bottom'])

def clamp_to_window(x: int, y: int) -> Tuple[int, int]:
    if not target_window or not target_window_rect:
        return x, y
    dpi_scale = target_window_rect.get('dpi', 96) / 96.0
    physical_x = int(x * dpi_scale)
    physical_y = int(y * dpi_scale)
    clamped_physical_x = max(target_window_rect['left'], min(physical_x, target_window_rect['right']))
    clamped_physical_y = max(target_window_rect['top'], min(physical_y, target_window_rect['bottom']))
    return int(clamped_physical_x / dpi_scale), int(clamped_physical_y / dpi_scale)

def get_logical_coords(x: int, y: int) -> Tuple[int, int]:
    if monitors_info and current_monitor_index < len(monitors_info):
        monitor = monitors_info[current_monitor_index]
        dpi_scale = monitor.get('DpiScale', 1.0)
        return int(x / dpi_scale), int(y / dpi_scale)
    return x, y

def mouse_click(x: int, y: int, button: str = "left", clicks: int = 1) -> bool:
    if not is_within_target_window(x, y):
        logger.warning(f"Click ({x}, {y}) outside target window, clamping to window bounds")
        x, y = clamp_to_window(x, y)
    
    try:
        if target_window and target_window_rect:
            dpi_scale = target_window_rect.get('dpi', 96) / 96.0
            physical_x = int(x * dpi_scale)
            physical_y = int(y * dpi_scale)
        else:
            monitor = monitors_info[current_monitor_index] if current_monitor_index < len(monitors_info) else monitors_info[0]
            dpi_scale = monitor.get('DpiScale', 1.0)
            physical_x = int(x * dpi_scale)
            physical_y = int(y * dpi_scale)
        logger.info(f"Click: logical ({x}, {y}) -> physical ({physical_x}, {physical_y})")
        pyautogui.click(physical_x, physical_y, button=button, clicks=clicks)
        return True
    except Exception as e:
        logger.error(f"Mouse click failed: {e}")
        return False

def mouse_move(x: int, y: int) -> bool:
    if not is_within_target_window(x, y):
        logger.warning(f"Move ({x}, {y}) outside target window, clamping to window bounds")
        x, y = clamp_to_window(x, y)
    
    try:
        if target_window and target_window_rect:
            dpi_scale = target_window_rect.get('dpi', 96) / 96.0
            physical_x = int(x * dpi_scale)
            physical_y = int(y * dpi_scale)
        else:
            monitor = monitors_info[current_monitor_index] if current_monitor_index < len(monitors_info) else monitors_info[0]
            dpi_scale = monitor.get('DpiScale', 1.0)
            physical_x = int(x * dpi_scale)
            physical_y = int(y * dpi_scale)
        pyautogui.moveTo(physical_x, physical_y)
        return True
    except Exception as e:
        logger.error(f"Mouse move failed: {e}")
        return False

def mouse_drag(start_x: int, start_y: int, end_x: int, end_y: int, button: str = "left") -> bool:
    if not is_within_target_window(start_x, start_y) or not is_within_target_window(end_x, end_y):
        logger.warning("Drag coordinates outside target window, clamping to window bounds")
        start_x, start_y = clamp_to_window(start_x, start_y)
        end_x, end_y = clamp_to_window(end_x, end_y)
    
    try:
        if target_window and target_window_rect:
            dpi_scale = target_window_rect.get('dpi', 96) / 96.0
            physical_sx = int(start_x * dpi_scale)
            physical_sy = int(start_y * dpi_scale)
            physical_ex = int(end_x * dpi_scale)
            physical_ey = int(end_y * dpi_scale)
        else:
            monitor = monitors_info[current_monitor_index] if current_monitor_index < len(monitors_info) else monitors_info[0]
            dpi_scale = monitor.get('DpiScale', 1.0)
            physical_sx = int(start_x * dpi_scale)
            physical_sy = int(start_y * dpi_scale)
            physical_ex = int(end_x * dpi_scale)
            physical_ey = int(end_y * dpi_scale)
        pyautogui.moveTo(physical_sx, physical_sy)
        pyautogui.drag(physical_ex - physical_sx, physical_ey - physical_sy, button=button)
        return True
    except Exception as e:
        logger.error(f"Mouse drag failed: {e}")
        return False

def mouse_scroll(x: int, y: int, delta: int = 120) -> bool:
    if not is_within_target_window(x, y):
        logger.warning(f"Scroll ({x}, {y}) outside target window, clamping to window bounds")
        x, y = clamp_to_window(x, y)
    
    try:
        if target_window and target_window_rect:
            dpi_scale = target_window_rect.get('dpi', 96) / 96.0
            physical_x = int(x * dpi_scale)
            physical_y = int(y * dpi_scale)
        else:
            monitor = monitors_info[current_monitor_index] if current_monitor_index < len(monitors_info) else monitors_info[0]
            dpi_scale = monitor.get('DpiScale', 1.0)
            physical_x = int(x * dpi_scale)
            physical_y = int(y * dpi_scale)
        pyautogui.moveTo(physical_x, physical_y)
        pyautogui.scroll(delta)
        return True
    except Exception as e:
        logger.error(f"Mouse scroll failed: {e}")
        return False

def keyboard_type(text: str) -> bool:
    try:
        pyautogui.typewrite(text, interval=0.05)
        return True
    except Exception as e:
        logger.error(f"键盘输入失败: {e}")
        return False

def keyboard_hotkey(*keys) -> bool:
    try:
        pyautogui.hotkey(*keys)
        return True
    except Exception as e:
        logger.error(f"快捷键执行失败: {e}")
        return False

def clear_and_type(text: str) -> bool:
    try:
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.1)
        pyautogui.typewrite(text, interval=0.05)
        return True
    except Exception as e:
        logger.error(f"Clear and type failed: {e}")
        return False

def verify_operation(expected_state: Dict[str, Any], timeout: float = 5.0) -> bool:
    start_time = time.time()
    while time.time() - start_time < timeout:
        result = capture_screen()
        if not result:
            time.sleep(0.5)
            continue
        
        img_path, _, _ = result
        elements = parse_ui_elements(img_path)
        os.remove(img_path)
        
        if "element_text" in expected_state:
            elem = find_element_by_text(elements, expected_state["element_text"])
            if elem:
                return True
        
        if "element_type" in expected_state:
            elems = find_element_by_type(elements, expected_state["element_type"])
            if elems:
                return True
        
        time.sleep(0.5)
    
    return False

def execute_ferment_command(data: Dict[str, Any]) -> Dict[str, Any]:
    result = {
        "status": OperationResult.FAILED,
        "message": "",
        "timestamp": datetime.now().isoformat(),
        "operation": data.get("action", data.get("操作", "unknown")),
        "retry_count": 0
    }
    
    max_retries = data.get("retries", data.get("重试次数", 3))
    timeout = data.get("timeout", data.get("超时", 10.0))
    
    action = data.get("action", data.get("操作", "")).lower()
    target = data.get("target", data.get("目标", ""))
    text = data.get("text", data.get("文本", data.get("dataName", data.get("数据名称", ""))))
    value = data.get("value", data.get("数值", data.get("dataSize", data.get("数据大小", ""))))
    element_type = data.get("elementType", data.get("元素类型", ""))
    x = data.get("x", data.get("坐标x", None))
    y = data.get("y", data.get("坐标y", None))
    delay = data.get("delay", data.get("延迟", 0.2))
    verify = data.get("verify", data.get("验证", None))
    
    for attempt in range(max_retries):
        result["retry_count"] = attempt + 1
        logger.info(f"Execute: {action}, target: {target}, attempt: {attempt + 1}/{max_retries}")
        
        try:
            if action in ["click", "点击"]:
                coords = None
                if x is not None and y is not None:
                    coords = (int(x), int(y))
                elif target:
                    coords = locate_target(target)
                elif element_type:
                    cap_res = capture_screen()
                    if cap_res:
                        img_path, _, _ = cap_res
                        elements = parse_ui_elements(img_path)
                        os.remove(img_path)
                        elems = find_element_by_type(elements, element_type)
                        if elems:
                            coords = elems[0].get_center()
                
                if coords:
                    time.sleep(delay)
                    if mouse_click(coords[0], coords[1]):
                        result["status"] = OperationResult.SUCCESS
                        result["message"] = f"Click success: ({coords[0]}, {coords[1]})"
                        result["coordinates"] = coords
                    else:
                        result["message"] = "Click failed"
                else:
                    result["message"] = f"Target not found: {target or element_type}"
            
            elif action in ["type", "输入", "设置", "set"]:
                input_text = text or str(value) if value else text
                if not input_text:
                    result["message"] = "Missing input text"
                    break
                
                coords = None
                if target:
                    coords = locate_target(target)
                elif element_type:
                    cap_result = capture_screen()
                    if cap_result:
                        img_path, _, _ = cap_result
                        elements = parse_ui_elements(img_path)
                        os.remove(img_path)
                        text_inputs = [e for e in find_element_by_type(elements, "text") if e.interactivity]
                        if text_inputs:
                            coords = text_inputs[0].get_center()
                
                if coords:
                    time.sleep(delay)
                    mouse_click(coords[0], coords[1])
                    time.sleep(0.1)
                    
                    if clear_and_type(input_text):
                        result["status"] = OperationResult.SUCCESS
                        result["message"] = f"Input success: {input_text}"
                    else:
                        result["message"] = "Input failed"
                else:
                    if clear_and_type(input_text):
                        result["status"] = OperationResult.SUCCESS
                        result["message"] = f"Input success (current position): {input_text}"
                    else:
                        result["message"] = "Input failed"
            
            elif action in ["locate", "定位"]:
                if target:
                    coords = locate_target(target)
                    if coords:
                        result["status"] = OperationResult.SUCCESS
                        result["message"] = f"Locate success: ({coords[0]}, {coords[1]})"
                        result["coordinates"] = coords
                    else:
                        result["message"] = f"Target not found: {target}"
                else:
                    result["message"] = "Missing locate target"
            
            elif action in ["move", "移动", "hover", "悬停"]:
                coords = None
                if x is not None and y is not None:
                    coords = (int(x), int(y))
                elif target:
                    coords = locate_target(target)
                
                if coords:
                    time.sleep(delay)
                    if mouse_move(coords[0], coords[1]):
                        result["status"] = OperationResult.SUCCESS
                        result["message"] = f"Move success: ({coords[0]}, {coords[1]})"
                        result["coordinates"] = coords
                    else:
                        result["message"] = "Move failed"
                else:
                    result["message"] = f"Target not found: {target}"
            
            elif action in ["drag", "拖拽", "拖动"]:
                start_x = data.get("startX", data.get("起始x", x))
                start_y = data.get("startY", data.get("起始y", y))
                end_x = data.get("endX", data.get("终点x", None))
                end_y = data.get("endY", data.get("终点y", None))
                drag_target = data.get("dragTarget", data.get("拖拽目标", None))
                
                if end_x is None or end_y is None:
                    if drag_target:
                        end_coords = locate_target(drag_target)
                        if end_coords:
                            end_x, end_y = end_coords
                    else:
                        result["message"] = "Missing end coordinates for drag"
                        break
                
                if start_x is not None and start_y is not None and end_x is not None and end_y is not None:
                    time.sleep(delay)
                    if mouse_drag(int(start_x), int(start_y), int(end_x), int(end_y)):
                        result["status"] = OperationResult.SUCCESS
                        result["message"] = f"Drag success: ({start_x},{start_y}) -> ({end_x},{end_y})"
                    else:
                        result["message"] = "Drag failed"
                else:
                    result["message"] = "Missing drag coordinates"
            
            elif action in ["scroll", "滚动"]:
                scroll_x = x if x is not None else screen_width // 2
                scroll_y = y if y is not None else screen_height // 2
                delta = data.get("delta", data.get("滚动量", 120))
                
                time.sleep(delay)
                if mouse_scroll(int(scroll_x), int(scroll_y), int(delta)):
                    result["status"] = OperationResult.SUCCESS
                    result["message"] = f"Scroll success at ({scroll_x}, {scroll_y}), delta={delta}"
                else:
                    result["message"] = "Scroll failed"
            
            elif action in ["analyze", "分析"]:
                cap_result = capture_screen()
                if cap_result:
                    img_path, _, _ = cap_result
                    elements = parse_ui_elements(img_path)
                    os.remove(img_path)
                    result["status"] = OperationResult.SUCCESS
                    result["message"] = f"Detected {len(elements)} UI elements"
                    result["elements"] = [
                        {"type": e.type, "text": e.text[:50], "interactivity": e.interactivity}
                        for e in elements[:20]
                    ]
                else:
                    result["message"] = "Screenshot failed"
            
            elif action in ["sequence", "组合操作"]:
                steps = data.get("steps", data.get("步骤", []))
                if not steps:
                    result["message"] = "Missing operation steps"
                    break
                
                clear_debug_steps()
                step_results = []
                for i, step in enumerate(steps):
                    step_result = execute_ferment_command(step)
                    step_results.append(step_result)
                    if step_result["status"] != OperationResult.SUCCESS:
                        result["message"] = f"Step {i+1} failed: {step_result['message']}"
                        result["step_results"] = step_results
                        break
                    time.sleep(step.get("delay", step.get("延迟", 0.5)))
                else:
                    result["status"] = OperationResult.SUCCESS
                    result["message"] = f"Sequence completed, {len(steps)} steps"
                    result["step_results"] = step_results
                
                draw_all_debug_steps()
            
            else:
                result["message"] = f"Unknown action: {action}"
            
            if result["status"] == OperationResult.SUCCESS:
                if verify:
                    time.sleep(0.5)
                    if not verify_operation(verify, timeout=3.0):
                        result["status"] = OperationResult.RETRY
                        result["message"] += " (verify failed)"
                        continue
                break
            
            if result["status"] == OperationResult.FAILED and attempt < max_retries - 1:
                time.sleep(1.0)
                continue
                
        except Exception as e:
            logger.error(f"Operation error: {e}")
            result["message"] = f"Operation error: {str(e)}"
            if attempt < max_retries - 1:
                time.sleep(1.0)
                continue
    
    logger.info(f"Result: {result['status']} - {result['message']}")
    return result

class FermentControlHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.info(f"HTTP: {args[0]}")
    
    def do_POST(self):
        if self.path == "/ferment/control":
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length).decode('utf-8')
                data = json.loads(body)
                
                logger.info(f"Received control command: {json.dumps(data, ensure_ascii=False)}")
                
                result = execute_ferment_command(data)
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
                
            except json.JSONDecodeError as e:
                self.send_error(400, f"JSON parse error: {e}")
            except Exception as e:
                logger.error(f"Request processing error: {e}")
                self.send_error(500, str(e))
        
        elif self.path.startswith("/ferment/window/select"):
            try:
                window_handle = None
                
                content_length = int(self.headers.get('Content-Length', 0))
                if content_length > 0:
                    body = self.rfile.read(content_length).decode('utf-8')
                    data = json.loads(body)
                    window_handle = data.get("handle", data.get("窗口句柄", None))
                
                if window_handle is None:
                    result = {
                        "status": "failed",
                        "message": "Missing window handle. Usage: POST with JSON {\"handle\": 12345} or add header: -H \"Content-Type: application/json\""
                    }
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.end_headers()
                    self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
                    return
                
                if set_target_window(int(window_handle)):
                    result = {
                        "status": "success",
                        "message": f"Target window set: handle={window_handle}",
                        "window_rect": target_window_rect
                    }
                else:
                    result = {
                        "status": "failed",
                        "message": f"Invalid window handle: {window_handle}"
                    }
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
                
            except json.JSONDecodeError as e:
                result = {
                    "status": "failed",
                    "message": f"JSON parse error. Add header: -H \"Content-Type: application/json\""
                }
                self.send_response(400)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
            except Exception as e:
                logger.error(f"Window selection error: {e}")
                self.send_error(500, str(e))
        
        elif self.path == "/ferment/window/clear":
            clear_target_window()
            result = {
                "status": "success",
                "message": "Target window cleared, using full screen"
            }
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
        
        else:
            self.send_error(404, "Not Found")
    
    def do_GET(self):
        if self.path == "/ferment/status":
            status = {
                "status": "running",
                "screen_size": [screen_width, screen_height],
                "models_loaded": processor is not None,
                "target_window": target_window,
                "target_window_rect": target_window_rect,
                "timestamp": datetime.now().isoformat()
            }
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(status, ensure_ascii=False).encode('utf-8'))
        
        elif self.path == "/ferment/window/list":
            windows = get_window_list()
            result = {
                "status": "success",
                "count": len(windows),
                "windows": windows
            }
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
        
        else:
            self.send_error(404, "Not Found")

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

def run_server(host: str = "0.0.0.0", port: int = 8080):
    server = ThreadedHTTPServer((host, port), FermentControlHandler)
    logger.info(f"Ferment control service started: http://{host}:{port}")
    logger.info("API endpoints:")
    logger.info("  POST /ferment/control       - Execute control command")
    logger.info("  GET  /ferment/status        - Get system status")
    logger.info("  GET  /ferment/window/list   - Get available windows")
    logger.info("  POST /ferment/window/select - Select target window")
    logger.info("  POST /ferment/window/clear  - Clear target window")
    server.serve_forever()

def interactive_mode():
    print("\n" + "=" * 60)
    print("Ferment Control Automation System - Interactive Mode")
    print("=" * 60)
    print("\nJSON format examples:")
    print('  {"action":"click", "target":"start fermentation button"}')
    print('  {"action":"type", "target":"temperature input", "text":"28.5"}')
    print('  {"action":"locate", "target":"stop button"}')
    print('  {"action":"move", "target":"hover area"}')
    print('  {"action":"drag", "startX":100, "startY":100, "endX":200, "endY":200}')
    print('  {"action":"scroll", "x":500, "y":300, "delta":120}')
    print('  {"action":"analyze"}')
    print('  {"action":"sequence", "steps":[{"action":"click","target":"settings"},{"action":"type","text":"30"}]}')
    print("  Type 'server' to start network service")
    print("  Type 'windows' to list and select target window")
    print("  Type 'q' to quit")
    print("-" * 60)
    
    while True:
        try:
            user_input = input("\nJSON> ").strip()
            if user_input.lower() == 'q':
                break
            if user_input.lower() == 'server':
                run_server()
                break
            if user_input.lower() == 'windows':
                select_target_window()
                continue
            if not user_input:
                continue
            
            data = json.loads(user_input)
            result = execute_ferment_command(data)
            print(f"[{result['status'].upper()}] {result['message']}")
            
        except json.JSONDecodeError as e:
            print(f"[ERROR] JSON parse failed: {e}")
        except KeyboardInterrupt:
            print("\nExit")
            break

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Ferment Control Automation System")
    parser.add_argument("--server", action="store_true", help="Start network service mode")
    parser.add_argument("--port", type=int, default=8080, help="Service port")
    parser.add_argument("--host", default="0.0.0.0", help="Service host")
    args = parser.parse_args()
    
    setup_models(server_mode=args.server)
    
    if args.server:
        run_server(args.host, args.port)
    else:
        interactive_mode()

if __name__ == "__main__":
    main()
