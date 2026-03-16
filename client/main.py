import asyncio
import argparse
import base64
import time
from typing import Dict, Any, Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "shared"))
sys.path.insert(0, str(Path(__file__).parent))

from protocol import MessageType
from constants import OperationResult
from utils import setup_logger, load_yaml_config, get_project_root, generate_message_id

from screenshot_service import ScreenshotService
from operation_executor import OperationExecutor
from server_connector import ServerConnector
from window_selector import WindowSelector


class Client:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = setup_logger('Client', config.get('logging'))
        
        self.client_id = config.get('client', {}).get('id', generate_message_id())
        self.client_name = config.get('client', {}).get('name', 'Desktop-Client')
        
        self.screenshot_service = ScreenshotService(config)
        self.operation_executor = OperationExecutor(config)
        self.server_connector = ServerConnector(config)
        self.window_selector: Optional[WindowSelector] = None
        
        self._running = False
        self._dpi_scale = 1.0
    
    async def initialize(self, select_window: bool = True) -> bool:
        self.logger.info(f"Initializing client: {self.client_name} ({self.client_id})")
        
        self.window_selector = WindowSelector(self.screenshot_service)
        
        if select_window:
            if not self.window_selector.auto_select_saved_window():
                if not self.window_selector.show_selection_ui():
                    self.logger.info("Using full screen mode")
        
        if self.screenshot_service.target_window_rect:
            self._dpi_scale = self.screenshot_service.target_window_rect.get('dpi', 96) / 96.0
        else:
            monitors = self.screenshot_service.monitors_info
            if monitors:
                self._dpi_scale = monitors[self.screenshot_service.current_monitor_index].get('DpiScale', 1.0)
        
        if not await self.server_connector.connect():
            self.logger.error("Failed to connect to server")
            return False
        
        window_info = self.screenshot_service.get_screen_info()
        if not await self.server_connector.register(self.client_id, window_info):
            self.logger.error("Failed to register with server")
            return False
        
        self._setup_message_handlers()
        
        self._running = True
        self.logger.info("Client initialized successfully!")
        return True
    
    def _setup_message_handlers(self):
        self.server_connector.register_message_handler(
            MessageType.SCREENSHOT_REQUEST,
            self._handle_screenshot_request
        )
        self.server_connector.register_message_handler(
            MessageType.COORDINATE_COMMAND,
            self._handle_coordinate_command
        )
        self.server_connector.register_message_handler(
            MessageType.HEARTBEAT_ACK,
            self._handle_heartbeat_ack
        )
    
    async def _handle_screenshot_request(self, message: Dict[str, Any]):
        request_id = message.get('payload', {}).get('request_id')
        self.logger.info(f"Received screenshot request: {request_id}")
        
        result = self.screenshot_service.capture_screen()
        
        if result:
            image_data, window_info = result
            image_base64 = base64.b64encode(image_data).decode('utf-8')
            
            success = await self.server_connector.send_screenshot_response(
                request_id=request_id,
                image_base64=image_base64,
                window_info=window_info.to_dict()
            )
            
            if success:
                self.logger.info(f"Screenshot sent: {len(image_base64)} bytes")
            else:
                self.logger.error("Failed to send screenshot response")
        else:
            self.logger.error("Screenshot capture failed")
    
    async def _handle_coordinate_command(self, message: Dict[str, Any]):
        payload = message.get('payload', {})
        operation = payload.get('operation', '')
        operation_id = payload.get('operation_id', '')
        
        self.logger.info(f"Received coordinate command: {operation}")
        
        start_time = time.time()
        status = OperationResult.FAILED
        result_message = ""
        coords = None
        
        try:
            if operation == 'click':
                x = payload.get('x', 0)
                y = payload.get('y', 0)
                button = payload.get('button', 'left')
                clicks = payload.get('clicks', 1)
                
                if self.operation_executor.execute_click(x, y, button, clicks, self._dpi_scale):
                    status = OperationResult.SUCCESS
                    result_message = f"Click success: ({x}, {y})"
                    coords = (x, y)
                else:
                    result_message = "Click failed"
            
            elif operation == 'move':
                x = payload.get('x', 0)
                y = payload.get('y', 0)
                
                if self.operation_executor.execute_move(x, y, self._dpi_scale):
                    status = OperationResult.SUCCESS
                    result_message = f"Move success: ({x}, {y})"
                    coords = (x, y)
                else:
                    result_message = "Move failed"
            
            elif operation == 'drag':
                start_x = payload.get('start_x', 0)
                start_y = payload.get('start_y', 0)
                end_x = payload.get('x', 0)
                end_y = payload.get('y', 0)
                button = payload.get('button', 'left')
                
                if self.operation_executor.execute_drag(start_x, start_y, end_x, end_y, button, self._dpi_scale):
                    status = OperationResult.SUCCESS
                    result_message = f"Drag success: ({start_x},{start_y}) -> ({end_x},{end_y})"
                else:
                    result_message = "Drag failed"
            
            elif operation == 'scroll':
                x = payload.get('x', 0)
                y = payload.get('y', 0)
                delta = payload.get('delta', 120)
                
                if self.operation_executor.execute_scroll(x, y, delta, self._dpi_scale):
                    status = OperationResult.SUCCESS
                    result_message = f"Scroll success at ({x}, {y})"
                else:
                    result_message = "Scroll failed"
            
            elif operation == 'type':
                text = payload.get('text', '')
                
                if self.operation_executor.execute_type(text):
                    status = OperationResult.SUCCESS
                    result_message = f"Type success: {text[:20]}"
                else:
                    result_message = "Type failed"
            
            elif operation == 'hotkey':
                keys = payload.get('keys', [])
                
                if self.operation_executor.execute_hotkey(*keys):
                    status = OperationResult.SUCCESS
                    result_message = f"Hotkey success: {keys}"
                else:
                    result_message = "Hotkey failed"
            
            elif operation == 'clear_and_type':
                text = payload.get('text', '')
                
                if self.operation_executor.execute_clear_and_type(text):
                    status = OperationResult.SUCCESS
                    result_message = f"Clear and type success: {text[:20]}"
                else:
                    result_message = "Clear and type failed"
            
            else:
                result_message = f"Unknown operation: {operation}"
        
        except Exception as e:
            self.logger.error(f"Operation execution error: {e}")
            result_message = f"Error: {str(e)}"
        
        execution_time_ms = (time.time() - start_time) * 1000
        
        await self.server_connector.send_operation_result(
            operation_id=operation_id,
            status=status,
            message=result_message,
            execution_time_ms=execution_time_ms,
            coordinates=coords
        )
        
        self.logger.info(f"Operation result: {status} - {result_message} ({execution_time_ms:.1f}ms)")
    
    async def _handle_heartbeat_ack(self, message: Dict[str, Any]):
        pass
    
    async def run(self):
        if not await self.initialize():
            self.logger.error("Client initialization failed")
            return
        
        self.logger.info("Client running...")
        
        heartbeat_task = asyncio.create_task(self.server_connector.start_heartbeat())
        
        try:
            await self.server_connector.listen()
        except KeyboardInterrupt:
            self.logger.info("Shutting down...")
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            await self.shutdown()
    
    async def shutdown(self):
        self._running = False
        self.logger.info("Shutting down client...")
        await self.server_connector.disconnect()
        self.logger.info("Client shutdown complete")


def load_client_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    if config_path is None:
        config_path = str(get_project_root() / "config.yaml")
    
    config = load_yaml_config(config_path)
    
    defaults = {
        'client': {
            'id': generate_message_id(),
            'name': 'Desktop-Client'
        },
        'server': {
            'host': '127.0.0.1',
            'websocket_port': 8081,
            'reconnect_interval': 5,
            'max_reconnect_attempts': 10
        },
        'screenshot': {
            'width': 1280
        },
        'logging': {
            'level': 'INFO',
            'file': str(get_project_root() / "logs" / "client.log")
        }
    }
    
    for key, value in defaults.items():
        if key not in config:
            config[key] = value
        elif isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if sub_key not in config[key]:
                    config[key][sub_key] = sub_value
    
    return config


def main():
    parser = argparse.ArgumentParser(description="GUI Actor Client")
    parser.add_argument("--config", type=str, default=None, help="Configuration file path")
    parser.add_argument("--server-host", type=str, default=None, help="Server host address")
    parser.add_argument("--server-port", type=int, default=None, help="Server WebSocket port")
    parser.add_argument("--no-window-select", action="store_true", help="Skip window selection")
    args = parser.parse_args()
    
    config = load_client_config(args.config)
    
    if args.server_host:
        host = args.server_host
        if ':' in host:
            host, port_from_host = host.split(':', 1)
            config['server']['host'] = host
            if port_from_host:
                try:
                    config['server']['websocket_port'] = int(port_from_host)
                except ValueError:
                    pass
        else:
            config['server']['host'] = host
    if args.server_port:
        config['server']['websocket_port'] = args.server_port
    
    client = Client(config)
    asyncio.run(client.run())


if __name__ == "__main__":
    main()
