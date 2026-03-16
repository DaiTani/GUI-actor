import uuid
import time
import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Tuple, Optional


class MessageType:
    SCREENSHOT_REQUEST = "screenshot_request"
    SCREENSHOT_RESPONSE = "screenshot_response"
    COORDINATE_COMMAND = "coordinate_command"
    OPERATION_RESULT = "operation_result"
    CONTROL_COMMAND = "control_command"
    COMPLETION_NOTIFY = "completion_notify"
    HEARTBEAT = "heartbeat"
    HEARTBEAT_ACK = "heartbeat_ack"
    ERROR = "error"
    REGISTER = "register"
    REGISTER_ACK = "register_ack"
    DISCONNECT = "disconnect"


@dataclass
class UIElement:
    elem_type: str
    text: str
    bbox: List[float]
    interactivity: bool
    confidence: float = 1.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'elem_type': self.elem_type,
            'text': self.text,
            'bbox': self.bbox,
            'interactivity': self.interactivity,
            'confidence': self.confidence
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'UIElement':
        return cls(
            elem_type=data.get('elem_type', data.get('type', 'unknown')),
            text=data.get('text', data.get('content', '')),
            bbox=data.get('bbox', []),
            interactivity=data.get('interactivity', False),
            confidence=data.get('confidence', 1.0)
        )
    
    def get_center(self, screen_width: int, screen_height: int) -> Tuple[int, int]:
        if self.bbox and len(self.bbox) >= 4:
            x = int((self.bbox[0] + self.bbox[2]) / 2 * screen_width)
            y = int((self.bbox[1] + self.bbox[3]) / 2 * screen_height)
            return (x, y)
        return (0, 0)


@dataclass
class Message:
    type: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    payload: Dict[str, Any] = field(default_factory=dict)
    checksum: str = ""
    
    def __post_init__(self):
        if not self.checksum:
            self.checksum = self._compute_checksum()
    
    def _compute_checksum(self) -> str:
        data = f"{self.type}{self.id}{self.timestamp}{json.dumps(self.payload, sort_keys=True)}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]
    
    def verify_checksum(self) -> bool:
        return self.checksum == self._compute_checksum()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'type': self.type,
            'id': self.id,
            'timestamp': self.timestamp,
            'payload': self.payload,
            'checksum': self.checksum
        }
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Message':
        return cls(
            type=data.get('type', ''),
            id=data.get('id', str(uuid.uuid4())),
            timestamp=data.get('timestamp', time.time()),
            payload=data.get('payload', {}),
            checksum=data.get('checksum', '')
        )
    
    @classmethod
    def from_json(cls, json_str: str) -> 'Message':
        return cls.from_dict(json.loads(json_str))


@dataclass
class ScreenshotRequestPayload:
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    def to_dict(self) -> Dict[str, Any]:
        return {'request_id': self.request_id}


@dataclass
class ScreenshotResponsePayload:
    request_id: str
    image_base64: str
    window_info: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'request_id': self.request_id,
            'image_base64': self.image_base64,
            'window_info': self.window_info
        }


@dataclass
class CoordinateCommandPayload:
    operation: str
    x: int
    y: int
    button: str = "left"
    clicks: int = 1
    text: str = ""
    delta: int = 0
    start_x: Optional[int] = None
    start_y: Optional[int] = None
    end_x: Optional[int] = None
    end_y: Optional[int] = None
    keys: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'operation': self.operation,
            'x': self.x,
            'y': self.y,
            'button': self.button,
            'clicks': self.clicks,
            'text': self.text,
            'delta': self.delta,
            'start_x': self.start_x,
            'start_y': self.start_y,
            'end_x': self.end_x,
            'end_y': self.end_y,
            'keys': self.keys
        }


@dataclass
class OperationResultPayload:
    status: str
    message: str
    execution_time_ms: float
    coordinates: Optional[Tuple[int, int]] = None
    elements: Optional[List[Dict[str, Any]]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            'status': self.status,
            'message': self.message,
            'execution_time_ms': self.execution_time_ms
        }
        if self.coordinates:
            result['coordinates'] = self.coordinates
        if self.elements:
            result['elements'] = self.elements
        return result


@dataclass
class WindowInfo:
    width: int
    height: int
    orig_width: int
    orig_height: int
    dpi: int = 96
    dpi_scale: float = 1.0
    offset_x: int = 0
    offset_y: int = 0
    target_window_handle: Optional[int] = None
    target_window_rect: Optional[Dict[str, int]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'width': self.width,
            'height': self.height,
            'orig_width': self.orig_width,
            'orig_height': self.orig_height,
            'dpi': self.dpi,
            'dpi_scale': self.dpi_scale,
            'offset_x': self.offset_x,
            'offset_y': self.offset_y,
            'target_window_handle': self.target_window_handle,
            'target_window_rect': self.target_window_rect
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WindowInfo':
        return cls(
            width=data.get('width', 1920),
            height=data.get('height', 1080),
            orig_width=data.get('orig_width', 1920),
            orig_height=data.get('orig_height', 1080),
            dpi=data.get('dpi', 96),
            dpi_scale=data.get('dpi_scale', 1.0),
            offset_x=data.get('offset_x', 0),
            offset_y=data.get('offset_y', 0),
            target_window_handle=data.get('target_window_handle'),
            target_window_rect=data.get('target_window_rect')
        )
