import time
from typing import Dict, Any, Optional, Tuple, List

import pyautogui

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "shared"))

from utils import setup_logger


class OperationExecutor:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = setup_logger('OperationExecutor', config.get('logging'))
        
        self._last_execution_time: float = 0
        self._execution_start_time: float = 0
    
    def _get_physical_coords(self, x: int, y: int, dpi_scale: float = 1.0) -> Tuple[int, int]:
        return int(x * dpi_scale), int(y * dpi_scale)
    
    def execute_click(self, x: int, y: int, button: str = "left", clicks: int = 1,
                      dpi_scale: float = 1.0) -> bool:
        self._execution_start_time = time.time()
        
        try:
            physical_x, physical_y = self._get_physical_coords(x, y, dpi_scale)
            self.logger.info(f"Click: logical ({x}, {y}) -> physical ({physical_x}, {physical_y})")
            
            pyautogui.click(physical_x, physical_y, button=button, clicks=clicks)
            
            self._last_execution_time = time.time() - self._execution_start_time
            return True
            
        except Exception as e:
            self.logger.error(f"Mouse click failed: {e}")
            self._last_execution_time = time.time() - self._execution_start_time
            return False
    
    def execute_move(self, x: int, y: int, dpi_scale: float = 1.0) -> bool:
        self._execution_start_time = time.time()
        
        try:
            physical_x, physical_y = self._get_physical_coords(x, y, dpi_scale)
            self.logger.info(f"Move: logical ({x}, {y}) -> physical ({physical_x}, {physical_y})")
            
            pyautogui.moveTo(physical_x, physical_y)
            
            self._last_execution_time = time.time() - self._execution_start_time
            return True
            
        except Exception as e:
            self.logger.error(f"Mouse move failed: {e}")
            self._last_execution_time = time.time() - self._execution_start_time
            return False
    
    def execute_drag(self, start_x: int, start_y: int, end_x: int, end_y: int,
                     button: str = "left", dpi_scale: float = 1.0) -> bool:
        self._execution_start_time = time.time()
        
        try:
            physical_sx, physical_sy = self._get_physical_coords(start_x, start_y, dpi_scale)
            physical_ex, physical_ey = self._get_physical_coords(end_x, end_y, dpi_scale)
            
            self.logger.info(f"Drag: ({start_x},{start_y}) -> ({end_x},{end_y})")
            
            pyautogui.moveTo(physical_sx, physical_sy)
            pyautogui.drag(physical_ex - physical_sx, physical_ey - physical_sy, button=button)
            
            self._last_execution_time = time.time() - self._execution_start_time
            return True
            
        except Exception as e:
            self.logger.error(f"Mouse drag failed: {e}")
            self._last_execution_time = time.time() - self._execution_start_time
            return False
    
    def execute_scroll(self, x: int, y: int, delta: int = 120,
                       dpi_scale: float = 1.0) -> bool:
        self._execution_start_time = time.time()
        
        try:
            physical_x, physical_y = self._get_physical_coords(x, y, dpi_scale)
            self.logger.info(f"Scroll at ({x}, {y}), delta={delta}")
            
            pyautogui.moveTo(physical_x, physical_y)
            pyautogui.scroll(delta)
            
            self._last_execution_time = time.time() - self._execution_start_time
            return True
            
        except Exception as e:
            self.logger.error(f"Mouse scroll failed: {e}")
            self._last_execution_time = time.time() - self._execution_start_time
            return False
    
    def execute_type(self, text: str) -> bool:
        self._execution_start_time = time.time()
        
        try:
            self.logger.info(f"Typing: {text[:20]}...")
            pyautogui.typewrite(text, interval=0.05)
            
            self._last_execution_time = time.time() - self._execution_start_time
            return True
            
        except Exception as e:
            self.logger.error(f"Keyboard type failed: {e}")
            self._last_execution_time = time.time() - self._execution_start_time
            return False
    
    def execute_hotkey(self, *keys) -> bool:
        self._execution_start_time = time.time()
        
        try:
            self.logger.info(f"Hotkey: {keys}")
            pyautogui.hotkey(*keys)
            
            self._last_execution_time = time.time() - self._execution_start_time
            return True
            
        except Exception as e:
            self.logger.error(f"Hotkey failed: {e}")
            self._last_execution_time = time.time() - self._execution_start_time
            return False
    
    def execute_clear_and_type(self, text: str) -> bool:
        self._execution_start_time = time.time()
        
        try:
            self.logger.info(f"Clear and type: {text[:20]}...")
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.1)
            pyautogui.typewrite(text, interval=0.05)
            
            self._last_execution_time = time.time() - self._execution_start_time
            return True
            
        except Exception as e:
            self.logger.error(f"Clear and type failed: {e}")
            self._last_execution_time = time.time() - self._execution_start_time
            return False
    
    def get_execution_time(self) -> float:
        return self._last_execution_time
    
    def get_execution_time_ms(self) -> float:
        return self._last_execution_time * 1000
