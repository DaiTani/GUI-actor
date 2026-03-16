import os
import sys
import uuid
import time
import json
import logging
import yaml
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List


SCRIPT_DIR = Path(__file__).parent.parent.resolve()


def generate_message_id() -> str:
    return str(uuid.uuid4())


def get_timestamp() -> float:
    return time.time()


def get_iso_timestamp() -> str:
    return datetime.now().isoformat()


def setup_logger(name: str, config: Optional[Dict[str, Any]] = None) -> logging.Logger:
    if config is None:
        config = {}
    
    log_level = getattr(logging, config.get('level', 'INFO'))
    log_file = config.get('file')
    
    logger = logging.getLogger(name)
    logger.setLevel(log_level)
    
    if logger.handlers:
        return logger
    
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger


def load_yaml_config(config_path: str) -> Dict[str, Any]:
    path = Path(config_path)
    if not path.is_absolute():
        path = SCRIPT_DIR / config_path
    
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    return {}


def save_json_config(config: Dict[str, Any], config_path: str) -> bool:
    try:
        path = Path(config_path)
        if not path.is_absolute():
            path = SCRIPT_DIR / config_path
        
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logging.error(f"Failed to save config: {e}")
        return False


def load_json_config(config_path: str) -> Optional[Dict[str, Any]]:
    try:
        path = Path(config_path)
        if not path.is_absolute():
            path = SCRIPT_DIR / config_path
        
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None
    except Exception as e:
        logging.error(f"Failed to load config: {e}")
        return None


def normalize_action(action: str) -> str:
    from constants import ACTION_ALIASES
    action_lower = action.lower()
    return ACTION_ALIASES.get(action_lower, action_lower)


def extract_keywords(text: str, min_length: int = 2) -> List[str]:
    from constants import STOPWORDS
    text_lower = text.lower()
    words = text_lower.split()
    return [w for w in words if len(w) > min_length and w not in STOPWORDS]


def calculate_distance(x1: int, y1: int, x2: int, y2: int) -> float:
    return ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5


def clamp_value(value: int, min_val: int, max_val: int) -> int:
    return max(min_val, min(value, max_val))


def ensure_directory(path: str) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_project_root() -> Path:
    return SCRIPT_DIR


def add_to_sys_path(path: str) -> None:
    abs_path = str(Path(path).resolve())
    if abs_path not in sys.path:
        sys.path.insert(0, abs_path)
