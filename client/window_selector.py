import json
from pathlib import Path
from typing import Optional, Dict, Any, List

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "shared"))

from utils import setup_logger, get_project_root, save_json_config, load_json_config


class WindowSelector:
    def __init__(self, screenshot_service):
        self.screenshot_service = screenshot_service
        self.logger = setup_logger('WindowSelector')
        
        self.config_file = get_project_root() / "window_config.json"
        self._selected_window: Optional[Dict[str, Any]] = None
    
    def show_selection_ui(self) -> bool:
        while True:
            self.logger.info("Fetching window list...")
            windows = self.screenshot_service.get_window_list()
            
            if not windows:
                self.logger.warning("No windows found")
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
            print("  'r' - Refresh window list")
            print("  'c' - Clear target window (use full screen)")
            print("  'q' - Skip window selection")
            print("=" * 60)
            
            try:
                choice = input("Select target window (number/r/c/q): ").strip().lower()
                
                if choice == 'q':
                    self.logger.info("Window selection skipped")
                    return False
                
                if choice == 'r':
                    continue
                
                if choice == 'c':
                    self.screenshot_service.clear_target_window()
                    self._selected_window = None
                    print("Cleared target window, using full screen")
                    return True
                
                idx = int(choice)
                if 0 <= idx < len(windows):
                    win = windows[idx]
                    handle = win.get('Handle')
                    if self.screenshot_service.set_target_window(handle):
                        self._selected_window = win
                        print(f"\nTarget window set: {win.get('Title', 'Unknown')}")
                        print(f"Window size: {win.get('Width', 0)}x{win.get('Height', 0)}")
                        print(f"Window position: ({win.get('X', 0)}, {win.get('Y', 0)})")
                        self.save_config(win)
                        return True
                    else:
                        self.logger.error("Failed to set target window")
                        return False
                else:
                    self.logger.warning(f"Invalid selection: {idx}")
            except ValueError:
                self.logger.warning("Invalid input")
            except Exception as e:
                self.logger.error(f"Window selection error: {e}")
                return False
        
        return False
    
    def get_selected_window(self) -> Optional[Dict[str, Any]]:
        return self._selected_window
    
    def save_config(self, window_info: Dict[str, Any]) -> bool:
        config = {
            'handle': window_info.get('Handle'),
            'title': window_info.get('Title'),
            'process_name': window_info.get('ProcessName'),
            'width': window_info.get('Width'),
            'height': window_info.get('Height'),
            'x': window_info.get('X'),
            'y': window_info.get('Y')
        }
        return save_json_config(config, str(self.config_file))
    
    def load_config(self) -> Optional[Dict[str, Any]]:
        config = load_json_config(str(self.config_file))
        if config:
            self.logger.info(f"Loaded window config: {config.get('title', 'Unknown')}")
        return config
    
    def auto_select_saved_window(self) -> bool:
        config = self.load_config()
        if not config:
            return False
        
        handle = config.get('handle')
        if handle:
            if self.screenshot_service.set_target_window(handle):
                self._selected_window = config
                self.logger.info(f"Auto-selected saved window: {config.get('title', 'Unknown')}")
                return True
        
        return False
    
    def list_windows(self) -> List[Dict[str, Any]]:
        return self.screenshot_service.get_window_list()
