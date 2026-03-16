import asyncio
import json
import time
from typing import Dict, Any, Optional, Callable

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "shared"))

from protocol import Message, MessageType
from constants import TimeoutConfig
from utils import setup_logger, generate_message_id

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False


class ServerConnector:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = setup_logger('ServerConnector', config.get('logging'))
        
        self.server_host = config.get('server', {}).get('host', '127.0.0.1')
        self.server_port = config.get('server', {}).get('websocket_port', 8081)
        self.reconnect_interval = config.get('server', {}).get('reconnect_interval', 5)
        self.max_reconnect_attempts = config.get('server', {}).get('max_reconnect_attempts', 10)
        
        self.websocket = None
        self._connected = False
        self._running = False
        self._reconnect_count = 0
        self._message_handlers: Dict[str, Callable] = {}
        self._heartbeat_task: Optional[asyncio.Task] = None
        
        self.timeout_config = TimeoutConfig()
    
    async def connect(self) -> bool:
        if not WEBSOCKETS_AVAILABLE:
            self.logger.error("websockets library not available")
            return False
        
        try:
            uri = f"ws://{self.server_host}:{self.server_port}"
            self.logger.info(f"Connecting to {uri}...")
            
            self.websocket = await asyncio.wait_for(
                websockets.connect(uri),
                timeout=self.timeout_config.WEBSOCKET_CONNECT
            )
            
            self._connected = True
            self._running = True
            self._reconnect_count = 0
            
            self.logger.info("Connected to server")
            return True
            
        except asyncio.TimeoutError:
            self.logger.error("Connection timeout")
            return False
        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            return False
    
    async def disconnect(self):
        self._running = False
        self._connected = False
        
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
        
        self.logger.info("Disconnected from server")
    
    async def reconnect(self) -> bool:
        self._reconnect_count += 1
        
        if self._reconnect_count > self.max_reconnect_attempts:
            self.logger.error(f"Max reconnect attempts ({self.max_reconnect_attempts}) reached")
            return False
        
        wait_time = min(self.reconnect_interval * (2 ** (self._reconnect_count - 1)), 60)
        self.logger.info(f"Reconnecting in {wait_time}s (attempt {self._reconnect_count})...")
        
        await asyncio.sleep(wait_time)
        return await self.connect()
    
    async def register(self, client_id: str, window_info: Dict[str, Any]) -> bool:
        if not self._connected:
            return False
        
        msg = Message(
            type=MessageType.REGISTER,
            payload={
                'client_id': client_id,
                'window_info': window_info
            }
        )
        
        try:
            await self.websocket.send(msg.to_json())
            self.logger.info(f"Registered with client_id: {client_id}")
            return True
        except Exception as e:
            self.logger.error(f"Registration failed: {e}")
            return False
    
    async def send_message(self, message: Dict[str, Any]) -> bool:
        if not self._connected or not self.websocket:
            self.logger.error("Not connected to server")
            return False
        
        try:
            msg = Message.from_dict(message)
            await self.websocket.send(msg.to_json())
            return True
        except Exception as e:
            self.logger.error(f"Send message failed: {e}")
            return False
    
    async def receive_message(self) -> Optional[Dict[str, Any]]:
        if not self._connected or not self.websocket:
            return None
        
        try:
            data = await self.websocket.recv()
            return json.loads(data)
        except websockets.exceptions.ConnectionClosed:
            self.logger.warning("Connection closed by server")
            self._connected = False
            return None
        except Exception as e:
            self.logger.error(f"Receive message failed: {e}")
            return None
    
    async def send_screenshot_response(self, request_id: str, image_base64: str,
                                       window_info: Dict[str, Any]) -> bool:
        msg = Message(
            type=MessageType.SCREENSHOT_RESPONSE,
            payload={
                'request_id': request_id,
                'image_base64': image_base64,
                'window_info': window_info
            }
        )
        
        return await self.send_message(msg.to_dict())
    
    async def send_operation_result(self, operation_id: str, status: str,
                                    message: str, execution_time_ms: float,
                                    coordinates: Optional[tuple] = None) -> bool:
        payload = {
            'operation_id': operation_id,
            'status': status,
            'message': message,
            'execution_time_ms': execution_time_ms
        }
        
        if coordinates:
            payload['coordinates'] = coordinates
        
        msg = Message(
            type=MessageType.OPERATION_RESULT,
            payload=payload
        )
        
        return await self.send_message(msg.to_dict())
    
    async def start_heartbeat(self):
        while self._running and self._connected:
            try:
                msg = Message(type=MessageType.HEARTBEAT)
                await self.websocket.send(msg.to_json())
                await asyncio.sleep(self.timeout_config.HEARTBEAT_INTERVAL)
            except Exception as e:
                self.logger.error(f"Heartbeat failed: {e}")
                self._connected = False
                break
    
    def register_message_handler(self, message_type: str, handler: Callable):
        self._message_handlers[message_type] = handler
    
    def is_connected(self) -> bool:
        return self._connected
    
    async def listen(self):
        while self._running:
            if not self._connected:
                if not await self.reconnect():
                    await asyncio.sleep(self.reconnect_interval)
                    continue
            
            message = await self.receive_message()
            if message is None:
                continue
            
            msg_type = message.get('type')
            if msg_type in self._message_handlers:
                try:
                    await self._message_handlers[msg_type](message)
                except Exception as e:
                    self.logger.error(f"Handler error for {msg_type}: {e}")
