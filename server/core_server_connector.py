import json
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, Callable
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import threading

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "shared"))

from constants import OperationResult, TimeoutConfig
from utils import setup_logger


class CoreServerHandler(BaseHTTPRequestHandler):
    command_callback: Optional[Callable] = None
    window_list_callback: Optional[Callable] = None
    window_select_callback: Optional[Callable] = None
    window_clear_callback: Optional[Callable] = None
    status_callback: Optional[Callable] = None
    logger = None
    
    def log_message(self, format, *args):
        if self.logger:
            self.logger.info(f"HTTP: {args[0]}")
    
    def do_POST(self):
        if self.path == "/ferment/control":
            self._handle_control_command()
        elif self.path.startswith("/ferment/window/select"):
            self._handle_window_select()
        elif self.path == "/ferment/window/clear":
            self._handle_window_clear()
        else:
            self.send_error(404, "Not Found")
    
    def do_GET(self):
        if self.path == "/ferment/status":
            self._handle_status()
        elif self.path == "/ferment/window/list":
            self._handle_window_list()
        else:
            self.send_error(404, "Not Found")
    
    def _handle_control_command(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            data = json.loads(body)
            
            if self.logger:
                self.logger.info(f"Received control command: {json.dumps(data, ensure_ascii=False)}")
            
            if self.command_callback:
                result = self.command_callback(data)
            else:
                result = {
                    "status": OperationResult.FAILED,
                    "message": "No command handler registered"
                }
            
            self._send_json_response(result)
            
        except json.JSONDecodeError as e:
            self._send_error_response(400, f"JSON parse error: {e}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"Request processing error: {e}")
            self._send_error_response(500, str(e))
    
    def _handle_window_select(self):
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
                    "message": "Missing window handle"
                }
                self._send_json_response(result, status_code=400)
                return
            
            if self.window_select_callback:
                result = self.window_select_callback(int(window_handle))
            else:
                result = {
                    "status": "failed",
                    "message": "No window select handler registered"
                }
            
            self._send_json_response(result)
            
        except json.JSONDecodeError:
            result = {
                "status": "failed",
                "message": "JSON parse error"
            }
            self._send_json_response(result, status_code=400)
        except Exception as e:
            if self.logger:
                self.logger.error(f"Window selection error: {e}")
            self._send_error_response(500, str(e))
    
    def _handle_window_clear(self):
        if self.window_clear_callback:
            self.window_clear_callback()
        
        result = {
            "status": "success",
            "message": "Target window cleared, using full screen"
        }
        self._send_json_response(result)
    
    def _handle_status(self):
        if self.status_callback:
            status = self.status_callback()
        else:
            status = {
                "status": "running",
                "timestamp": datetime.now().isoformat()
            }
        self._send_json_response(status)
    
    def _handle_window_list(self):
        if self.window_list_callback:
            windows = self.window_list_callback()
            result = {
                "status": "success",
                "count": len(windows),
                "windows": windows
            }
        else:
            result = {
                "status": "failed",
                "message": "No window list handler registered"
            }
        self._send_json_response(result)
    
    def _send_json_response(self, data: Dict[str, Any], status_code: int = 200):
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
    
    def _send_error_response(self, code: int, message: str):
        self.send_error(code, message)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class CoreServerConnector:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = setup_logger('CoreServerConnector', config.get('logging'))
        
        self.host = config.get('server', {}).get('host', '0.0.0.0')
        self.port = config.get('server', {}).get('http_port', 8080)
        
        self.server: Optional[ThreadedHTTPServer] = None
        self._running = False
        self._server_thread: Optional[threading.Thread] = None
    
    def set_command_callback(self, callback: Callable):
        CoreServerHandler.command_callback = callback
    
    def set_window_list_callback(self, callback: Callable):
        CoreServerHandler.window_list_callback = callback
    
    def set_window_select_callback(self, callback: Callable):
        CoreServerHandler.window_select_callback = callback
    
    def set_window_clear_callback(self, callback: Callable):
        CoreServerHandler.window_clear_callback = callback
    
    def set_status_callback(self, callback: Callable):
        CoreServerHandler.status_callback = callback
    
    def start_http_server(self) -> bool:
        try:
            CoreServerHandler.logger = self.logger
            self.server = ThreadedHTTPServer((self.host, self.port), CoreServerHandler)
            self._running = True
            
            self._server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self._server_thread.start()
            
            self.logger.info(f"HTTP server started on {self.host}:{self.port}")
            self.logger.info("API endpoints:")
            self.logger.info("  POST /ferment/control       - Execute control command")
            self.logger.info("  GET  /ferment/status        - Get system status")
            self.logger.info("  GET  /ferment/window/list   - Get available windows")
            self.logger.info("  POST /ferment/window/select - Select target window")
            self.logger.info("  POST /ferment/window/clear  - Clear target window")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to start HTTP server: {e}")
            return False
    
    def stop_http_server(self):
        self._running = False
        if self.server:
            self.server.shutdown()
            self.logger.info("HTTP server stopped")
    
    def is_running(self) -> bool:
        return self._running
