import asyncio
import argparse
from datetime import datetime
from typing import Dict, Any, Optional

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.resolve()
GUI_ACTOR_DIR = SCRIPT_DIR / "GUI-Actor"
sys.path.insert(0, str(GUI_ACTOR_DIR / "src"))
sys.path.insert(0, str(SCRIPT_DIR / "OmniParser"))
sys.path.insert(0, str(SCRIPT_DIR / "shared"))
sys.path.insert(0, str(Path(__file__).parent))

from constants import OperationResult
from protocol import WindowInfo
from utils import setup_logger, load_yaml_config, get_project_root

from model_service import ModelService
from client_manager import ClientManager
from core_server_connector import CoreServerConnector
from command_processor import CommandProcessor


class Server:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = setup_logger('Server', config.get('logging'))
        
        self.model_service = ModelService(config)
        self.client_manager = ClientManager(config)
        self.core_server_connector = CoreServerConnector(config)
        self.command_processor: Optional[CommandProcessor] = None
        
        self._running = False
    
    async def initialize(self) -> bool:
        self.logger.info("Initializing server...")
        
        if not self.model_service.initialize():
            self.logger.error("Failed to initialize model service")
            return False
        
        if not await self.client_manager.start_server():
            self.logger.error("Failed to start client manager")
            return False
        
        self.command_processor = CommandProcessor(
            self.model_service, self.client_manager, self.config
        )
        
        self._setup_callbacks()
        
        if not self.core_server_connector.start_http_server():
            self.logger.error("Failed to start HTTP server")
            return False
        
        self._running = True
        self.logger.info("Server initialized successfully!")
        return True
    
    def _setup_callbacks(self):
        self.core_server_connector.set_command_callback(self._handle_control_command)
        self.core_server_connector.set_status_callback(self._handle_status_request)
    
    def _handle_control_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not self._running:
            return {
                "status": OperationResult.FAILED,
                "message": "Server not running"
            }
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self.command_processor.process_command(data))
            return result
        finally:
            loop.close()
    
    def _handle_status_request(self) -> Dict[str, Any]:
        clients = self.client_manager.get_connected_clients()
        return {
            "status": "running" if self._running else "stopped",
            "connected_clients": len(clients),
            "models_loaded": self.model_service.processor is not None,
            "timestamp": datetime.now().isoformat()
        }
    
    async def run(self):
        if not await self.initialize():
            self.logger.error("Server initialization failed")
            return
        
        self.logger.info("Server running...")
        
        try:
            while self._running:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            self.logger.info("Shutting down...")
        finally:
            await self.shutdown()
    
    async def shutdown(self):
        self._running = False
        self.logger.info("Shutting down server...")
        
        await self.client_manager.stop_server()
        self.core_server_connector.stop_http_server()
        
        self.logger.info("Server shutdown complete")


def load_server_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    if config_path is None:
        config_path = str(get_project_root() / "config.yaml")
    
    config = load_yaml_config(config_path)
    
    defaults = {
        'server': {
            'host': '0.0.0.0',
            'http_port': 8080,
            'websocket_port': 8081
        },
        'model': {
            'gui_actor': 'microsoft/GUI-Actor-2B-Qwen2-VL',
            'enable_omniparser': True,
            'omniparser': {
                'som_model_path': str(get_project_root() / "OmniParser" / "weights" / "icon_detect" / "model.pt"),
                'caption_model_path': str(get_project_root() / "OmniParser" / "weights" / "icon_caption_florence"),
                'box_threshold': 0.05
            }
        },
        'operation': {
            'default_delay': 0.2,
            'max_retries': 3
        },
        'logging': {
            'level': 'INFO',
            'file': str(get_project_root() / "logs" / "server.log")
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
    parser = argparse.ArgumentParser(description="GUI Actor Server")
    parser.add_argument("--config", type=str, default=None, help="Configuration file path")
    parser.add_argument("--port", type=int, default=None, help="HTTP port")
    parser.add_argument("--host", type=str, default=None, help="Host address")
    args = parser.parse_args()
    
    config = load_server_config(args.config)
    
    if args.port:
        config['server']['http_port'] = args.port
    if args.host:
        config['server']['host'] = args.host
    
    server = Server(config)
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
