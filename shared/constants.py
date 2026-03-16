from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple, Optional


class OperationResult:
    SUCCESS = "success"
    FAILED = "failed"
    RETRY = "retry"
    TIMEOUT = "timeout"


@dataclass
class DefaultConfig:
    SERVER_HOST: str = "0.0.0.0"
    SERVER_HTTP_PORT: int = 8080
    SERVER_WEBSOCKET_PORT: int = 8081
    SCREENSHOT_WIDTH: int = 1280
    MAX_RETRIES: int = 3
    DEFAULT_DELAY: float = 0.2


@dataclass
class TimeoutConfig:
    HTTP_REQUEST: float = 30.0
    SCREENSHOT_REQUEST: float = 10.0
    SCREENSHOT_EXECUTION: float = 10.0
    DATA_TRANSFER: float = 30.0
    MODEL_ANALYSIS: float = 120.0
    COORDINATE_COMMAND: float = 10.0
    OPERATION_EXECUTION: float = 5.0
    COMPLETION_NOTIFY: float = 10.0
    WEBSOCKET_CONNECT: float = 10.0
    HEARTBEAT_INTERVAL: float = 30.0


@dataclass
class RetryConfig:
    HTTP_REQUEST: int = 3
    SCREENSHOT_REQUEST: int = 3
    SCREENSHOT_EXECUTION: int = 2
    DATA_TRANSFER: int = 3
    COORDINATE_COMMAND: int = 3
    OPERATION_EXECUTION: int = 3
    COMPLETION_NOTIFY: int = 3
    RECONNECT: int = 10


class MouseButton(Enum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class ActionType(Enum):
    CLICK = "click"
    TYPE = "type"
    LOCATE = "locate"
    MOVE = "move"
    DRAG = "drag"
    SCROLL = "scroll"
    ANALYZE = "analyze"
    SEQUENCE = "sequence"
    HOTKEY = "hotkey"
    CLEAR_AND_TYPE = "clear_and_type"


ACTION_ALIASES: Dict[str, str] = {
    "click": ActionType.CLICK.value,
    "点击": ActionType.CLICK.value,
    "type": ActionType.TYPE.value,
    "输入": ActionType.TYPE.value,
    "设置": ActionType.TYPE.value,
    "set": ActionType.TYPE.value,
    "locate": ActionType.LOCATE.value,
    "定位": ActionType.LOCATE.value,
    "move": ActionType.MOVE.value,
    "移动": ActionType.MOVE.value,
    "hover": ActionType.MOVE.value,
    "悬停": ActionType.MOVE.value,
    "drag": ActionType.DRAG.value,
    "拖拽": ActionType.DRAG.value,
    "拖动": ActionType.DRAG.value,
    "scroll": ActionType.SCROLL.value,
    "滚动": ActionType.SCROLL.value,
    "analyze": ActionType.ANALYZE.value,
    "分析": ActionType.ANALYZE.value,
    "sequence": ActionType.SEQUENCE.value,
    "组合操作": ActionType.SEQUENCE.value,
    "hotkey": ActionType.HOTKEY.value,
    "快捷键": ActionType.HOTKEY.value,
    "clear_and_type": ActionType.CLEAR_AND_TYPE.value,
}


INPUT_KEYWORDS = ['search', 'input', 'text', 'edit', 'box', 'field', 'type', 'enter', 'write', '输入', '文本', '搜索']
BUTTON_KEYWORDS = ['button', 'click', 'submit', 'send', 'confirm', 'ok', 'cancel', '按钮', '点击', '确定', '取消', '开始', '启动', '提交']
STOPWORDS = ['the', 'for', 'and', 'not', 'are', 'you', 'area', 'page', 'very', 'middle']
