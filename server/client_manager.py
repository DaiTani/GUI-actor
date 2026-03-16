import asyncio
import json
import time
from typing import Dict, Any, Optional, Set
from dataclasses import dataclass, field

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "shared"))

from protocol import Message, MessageType, ScreenshotRequestPayload, ScreenshotResponsePayload, CoordinateCommandPayload, OperationResultPayload, WindowInfo
from constants import TimeoutConfig
from utils import setup_logger, generate_message_id

try:
    import websockets
    from websockets.server import serve
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False


@dataclass
class ClientInfo:
    client_id: str
    websocket: Any = None
    last_heartbeat: float = field(default_factory=time.time)
    pending_requests: Dict[str, asyncio.Future] = field(default_factory=dict)
    window_info: Optional[WindowInfo] = None


class ClientManager:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = setup_logger('ClientManager', config.get('logging'))
        
        self.host = config.get('server', {}).get('host', '0.0.0.0')
        self.port = config.get('server', {}).get('websocket_port', 8081)
        
        self.clients: Dict[str, ClientInfo] = {}
        self.server = None
        self._running = False
        self._message_handlers: Dict[str, callable] = {}
        
        self.timeout_config = TimeoutConfig()
    
    async def start_server(self) -> bool:
        if not WEBSOCKETS_AVAILABLE:
            self.logger.error("websockets library not available")
            return False
        
        try:
            self._running = True
            self.server = await serve(
                self._handle_client,
                self.host,
                self.port,
                ping_interval=self.timeout_config.HEARTBEAT_INTERVAL,
                ping_timeout=10
            )
            self.logger.info(f"WebSocket server started on {self.host}:{self.port}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to start WebSocket server: {e}")
            return False
    
    async def stop_server(self):
        self._running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.logger.info("WebSocket server stopped")
    
    async def _handle_client(self, websocket, path):
        client_id = generate_message_id()
        client_info = ClientInfo(client_id=client_id, websocket=websocket)
        self.clients[client_id] = client_info
        
        self.logger.info(f"Client connected: {client_id}")
        
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    msg = Message.from_dict(data)
                    
                    if msg.type == MessageType.HEARTBEAT:
                        client_info.last_heartbeat = time.time()
                        await self._send_heartbeat_ack(websocket)
                    
                    elif msg.type == MessageType.REGISTER:
                        await self._handle_register(client_id, msg)
                    
                    elif msg.type == MessageType.SCREENSHOT_RESPONSE:
                        await self._handle_screenshot_response(client_id, msg)
                    
                    elif msg.type == MessageType.OPERATION_RESULT:
                        await self._handle_operation_result(client_id, msg)
                    
                    else:
                        if msg.type in self._message_handlers:
                            await self._message_handlers[msg.type](client_id, msg)
                
                except json.JSONDecodeError as e:
                    self.logger.error(f"Invalid JSON from client {client_id}: {e}")
                except Exception as e:
                    self.logger.error(f"Error handling message from {client_id}: {e}")
        
        except websockets.exceptions.ConnectionClosed:
            self.logger.info(f"Client disconnected: {client_id}")
        finally:
            await self._handle_client_disconnect(client_id)
    
    async def _send_heartbeat_ack(self, websocket):
        msg = Message(type=MessageType.HEARTBEAT_ACK)
        await websocket.send(msg.to_json())
    
    async def _handle_register(self, client_id: str, msg: Message):
        payload = msg.payload
        self.logger.info(f"Client {client_id} registered with info: {payload}")
        
        if 'window_info' in payload:
            self.clients[client_id].window_info = WindowInfo.from_dict(payload['window_info'])
    
    async def _handle_screenshot_response(self, client_id: str, msg: Message):
        payload = msg.payload
        request_id = payload.get('request_id')
        
        if request_id and request_id in self.clients[client_id].pending_requests:
            future = self.clients[client_id].pending_requests.pop(request_id)
            if not future.done():
                future.set_result(payload)
    
    async def _handle_operation_result(self, client_id: str, msg: Message):
        payload = msg.payload
        
        if 'operation_id' in payload:
            operation_id = payload['operation_id']
            if operation_id in self.clients[client_id].pending_requests:
                future = self.clients[client_id].pending_requests.pop(operation_id)
                if not future.done():
                    future.set_result(payload)
    
    async def _handle_client_disconnect(self, client_id: str):
        if client_id in self.clients:
            client_info = self.clients[client_id]
            for future in client_info.pending_requests.values():
                if not future.done():
                    future.set_exception(ConnectionError("Client disconnected"))
            del self.clients[client_id]
            self.logger.info(f"Client {client_id} removed from manager")
    
    def register_message_handler(self, message_type: str, handler: callable):
        self._message_handlers[message_type] = handler
    
    async def send_screenshot_request(self, client_id: str) -> Optional[Dict[str, Any]]:
        if client_id not in self.clients:
            self.logger.error(f"Client {client_id} not found")
            return None
        
        client_info = self.clients[client_id]
        request_id = generate_message_id()
        
        future = asyncio.Future()
        client_info.pending_requests[request_id] = future
        
        msg = Message(
            type=MessageType.SCREENSHOT_REQUEST,
            payload=ScreenshotRequestPayload(request_id=request_id).to_dict()
        )
        
        try:
            await client_info.websocket.send(msg.to_json())
            self.logger.info(f"Sent screenshot request to {client_id}")
            
            result = await asyncio.wait_for(future, timeout=self.timeout_config.SCREENSHOT_EXECUTION)
            return result
        except asyncio.TimeoutError:
            self.logger.error(f"Screenshot request timeout for {client_id}")
            client_info.pending_requests.pop(request_id, None)
            return None
        except Exception as e:
            self.logger.error(f"Failed to send screenshot request: {e}")
            client_info.pending_requests.pop(request_id, None)
            return None
    
    async def send_coordinates(self, client_id: str, coords: Dict[str, Any]) -> bool:
        if client_id not in self.clients:
            self.logger.error(f"Client {client_id} not found")
            return False
        
        client_info = self.clients[client_id]
        operation_id = generate_message_id()
        
        future = asyncio.Future()
        client_info.pending_requests[operation_id] = future
        
        coords['operation_id'] = operation_id
        msg = Message(
            type=MessageType.COORDINATE_COMMAND,
            payload=coords
        )
        
        try:
            await client_info.websocket.send(msg.to_json())
            self.logger.info(f"Sent coordinate command to {client_id}: {coords}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to send coordinates: {e}")
            client_info.pending_requests.pop(operation_id, None)
            return False
    
    async def wait_for_operation_result(self, client_id: str, operation_id: str, 
                                        timeout: float = None) -> Optional[Dict[str, Any]]:
        if client_id not in self.clients:
            return None
        
        client_info = self.clients[client_id]
        
        if operation_id in client_info.pending_requests:
            future = client_info.pending_requests[operation_id]
            try:
                result = await asyncio.wait_for(
                    future, 
                    timeout=timeout or self.timeout_config.OPERATION_EXECUTION
                )
                return result
            except asyncio.TimeoutError:
                self.logger.error(f"Operation result timeout for {operation_id}")
                return None
        
        return None
    
    def get_connected_clients(self) -> Set[str]:
        return set(self.clients.keys())
    
    def get_client_window_info(self, client_id: str) -> Optional[WindowInfo]:
        if client_id in self.clients:
            return self.clients[client_id].window_info
        return None
    
    async def broadcast_to_clients(self, message: Dict[str, Any]) -> int:
        msg = Message.from_dict(message)
        success_count = 0
        
        for client_id, client_info in list(self.clients.items()):
            try:
                await client_info.websocket.send(msg.to_json())
                success_count += 1
            except Exception as e:
                self.logger.error(f"Failed to broadcast to {client_id}: {e}")
        
        return success_count
