import asyncio
import time
import base64
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "shared"))
sys.path.insert(0, str(Path(__file__).parent))

from constants import OperationResult, DefaultConfig
from protocol import WindowInfo
from utils import setup_logger, normalize_action

from model_service import ModelService
from client_manager import ClientManager


class CommandProcessor:
    def __init__(self, model_service: ModelService, client_manager: ClientManager, config: Dict[str, Any]):
        self.model_service = model_service
        self.client_manager = client_manager
        self.config = config
        self.logger = setup_logger('CommandProcessor', config.get('logging'))
        
        self.default_config = DefaultConfig()
        self.default_delay = config.get('operation', {}).get('default_delay', self.default_config.DEFAULT_DELAY)
        self.max_retries = config.get('operation', {}).get('max_retries', self.default_config.MAX_RETRIES)
        
        self._pending_operations: Dict[str, asyncio.Future] = {}
    
    async def process_command(self, command: Dict[str, Any]) -> Dict[str, Any]:
        result = {
            "status": OperationResult.FAILED,
            "message": "",
            "timestamp": datetime.now().isoformat(),
            "operation": command.get("action", command.get("操作", "unknown")),
            "retry_count": 0
        }
        
        max_retries = command.get("retries", command.get("重试次数", self.max_retries))
        timeout = command.get("timeout", command.get("超时", 10.0))
        
        action = normalize_action(command.get("action", command.get("操作", "")))
        
        clients = self.client_manager.get_connected_clients()
        if not clients:
            result["message"] = "No connected clients"
            return result
        
        client_id = list(clients)[0]
        
        for attempt in range(max_retries):
            result["retry_count"] = attempt + 1
            self.logger.info(f"Execute: {action}, attempt: {attempt + 1}/{max_retries}")
            
            try:
                if action == "click":
                    result = await self._execute_click(client_id, command, result)
                elif action == "type":
                    result = await self._execute_type(client_id, command, result)
                elif action == "locate":
                    result = await self._execute_locate(client_id, command, result)
                elif action == "move":
                    result = await self._execute_move(client_id, command, result)
                elif action == "drag":
                    result = await self._execute_drag(client_id, command, result)
                elif action == "scroll":
                    result = await self._execute_scroll(client_id, command, result)
                elif action == "analyze":
                    result = await self._execute_analyze(client_id, command, result)
                elif action == "sequence":
                    result = await self._execute_sequence(client_id, command, result)
                elif action == "hotkey":
                    result = await self._execute_hotkey(client_id, command, result)
                elif action == "clear_and_type":
                    result = await self._execute_clear_and_type(client_id, command, result)
                else:
                    result["message"] = f"Unknown action: {action}"
                
                if result["status"] == OperationResult.SUCCESS:
                    break
                
                if result["status"] == OperationResult.FAILED and attempt < max_retries - 1:
                    await asyncio.sleep(1.0)
                    continue
                    
            except Exception as e:
                self.logger.error(f"Operation error: {e}")
                result["message"] = f"Operation error: {str(e)}"
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.0)
                    continue
        
        self.logger.info(f"Result: {result['status']} - {result['message']}")
        return result
    
    async def _get_screenshot_and_locate(self, client_id: str, target: str) -> Optional[Tuple[int, int]]:
        screenshot_result = await self.client_manager.send_screenshot_request(client_id)
        if not screenshot_result:
            self.logger.error("Failed to get screenshot")
            return None
        
        image_base64 = screenshot_result.get('image_base64')
        window_info_dict = screenshot_result.get('window_info', {})
        window_info = WindowInfo.from_dict(window_info_dict)
        
        if not image_base64:
            self.logger.error("No image data in screenshot response")
            return None
        
        image_data = base64.b64decode(image_base64)
        
        coords = self.model_service.locate_target(target, image_data, window_info)
        return coords
    
    async def _execute_click(self, client_id: str, data: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        target = data.get("target", data.get("目标", ""))
        x = data.get("x", data.get("坐标x", None))
        y = data.get("y", data.get("坐标y", None))
        button = data.get("button", "left")
        clicks = data.get("clicks", 1)
        delay = data.get("delay", data.get("延迟", self.default_delay))
        
        coords = None
        if x is not None and y is not None:
            coords = (int(x), int(y))
        elif target:
            coords = await self._get_screenshot_and_locate(client_id, target)
        
        if coords:
            await asyncio.sleep(delay)
            
            coord_payload = {
                "operation": "click",
                "x": coords[0],
                "y": coords[1],
                "button": button,
                "clicks": clicks
            }
            
            if await self.client_manager.send_coordinates(client_id, coord_payload):
                result["status"] = OperationResult.SUCCESS
                result["message"] = f"Click success: ({coords[0]}, {coords[1]})"
                result["coordinates"] = coords
            else:
                result["message"] = "Failed to send click command"
        else:
            result["message"] = f"Target not found: {target}"
        
        return result
    
    async def _execute_type(self, client_id: str, data: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        target = data.get("target", data.get("目标", ""))
        text = data.get("text", data.get("文本", data.get("dataName", data.get("数据名称", ""))))
        value = data.get("value", data.get("数值", data.get("dataSize", data.get("数据大小", ""))))
        input_text = text or str(value) if value else text
        delay = data.get("delay", data.get("延迟", self.default_delay))
        
        if not input_text:
            result["message"] = "Missing input text"
            return result
        
        coords = None
        if target:
            coords = await self._get_screenshot_and_locate(client_id, target)
        
        if coords:
            await asyncio.sleep(delay)
            
            click_payload = {
                "operation": "click",
                "x": coords[0],
                "y": coords[1]
            }
            await self.client_manager.send_coordinates(client_id, click_payload)
            await asyncio.sleep(0.1)
        
        type_payload = {
            "operation": "type",
            "x": 0,
            "y": 0,
            "text": input_text
        }
        
        if await self.client_manager.send_coordinates(client_id, type_payload):
            result["status"] = OperationResult.SUCCESS
            result["message"] = f"Input success: {input_text}"
        else:
            result["message"] = "Input failed"
        
        return result
    
    async def _execute_locate(self, client_id: str, data: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        target = data.get("target", data.get("目标", ""))
        
        if not target:
            result["message"] = "Missing locate target"
            return result
        
        coords = await self._get_screenshot_and_locate(client_id, target)
        
        if coords:
            result["status"] = OperationResult.SUCCESS
            result["message"] = f"Locate success: ({coords[0]}, {coords[1]})"
            result["coordinates"] = coords
        else:
            result["message"] = f"Target not found: {target}"
        
        return result
    
    async def _execute_move(self, client_id: str, data: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        target = data.get("target", data.get("目标", ""))
        x = data.get("x", data.get("坐标x", None))
        y = data.get("y", data.get("坐标y", None))
        delay = data.get("delay", data.get("延迟", self.default_delay))
        
        coords = None
        if x is not None and y is not None:
            coords = (int(x), int(y))
        elif target:
            coords = await self._get_screenshot_and_locate(client_id, target)
        
        if coords:
            await asyncio.sleep(delay)
            
            move_payload = {
                "operation": "move",
                "x": coords[0],
                "y": coords[1]
            }
            
            if await self.client_manager.send_coordinates(client_id, move_payload):
                result["status"] = OperationResult.SUCCESS
                result["message"] = f"Move success: ({coords[0]}, {coords[1]})"
                result["coordinates"] = coords
            else:
                result["message"] = "Move failed"
        else:
            result["message"] = f"Target not found: {target}"
        
        return result
    
    async def _execute_drag(self, client_id: str, data: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        start_x = data.get("startX", data.get("起始x", data.get("x", None)))
        start_y = data.get("startY", data.get("起始y", data.get("y", None)))
        end_x = data.get("endX", data.get("终点x", None))
        end_y = data.get("endY", data.get("终点y", None))
        drag_target = data.get("dragTarget", data.get("拖拽目标", None))
        button = data.get("button", "left")
        delay = data.get("delay", data.get("延迟", self.default_delay))
        
        if end_x is None or end_y is None:
            if drag_target:
                end_coords = await self._get_screenshot_and_locate(client_id, drag_target)
                if end_coords:
                    end_x, end_y = end_coords
            else:
                result["message"] = "Missing end coordinates for drag"
                return result
        
        if start_x is not None and start_y is not None and end_x is not None and end_y is not None:
            await asyncio.sleep(delay)
            
            drag_payload = {
                "operation": "drag",
                "x": int(end_x),
                "y": int(end_y),
                "start_x": int(start_x),
                "start_y": int(start_y),
                "button": button
            }
            
            if await self.client_manager.send_coordinates(client_id, drag_payload):
                result["status"] = OperationResult.SUCCESS
                result["message"] = f"Drag success: ({start_x},{start_y}) -> ({end_x},{end_y})"
            else:
                result["message"] = "Drag failed"
        else:
            result["message"] = "Missing drag coordinates"
        
        return result
    
    async def _execute_scroll(self, client_id: str, data: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        x = data.get("x", data.get("坐标x", None))
        y = data.get("y", data.get("坐标y", None))
        delta = data.get("delta", data.get("滚动量", 120))
        delay = data.get("delay", data.get("延迟", self.default_delay))
        
        scroll_x = x if x is not None else 960
        scroll_y = y if y is not None else 540
        
        await asyncio.sleep(delay)
        
        scroll_payload = {
            "operation": "scroll",
            "x": int(scroll_x),
            "y": int(scroll_y),
            "delta": int(delta)
        }
        
        if await self.client_manager.send_coordinates(client_id, scroll_payload):
            result["status"] = OperationResult.SUCCESS
            result["message"] = f"Scroll success at ({scroll_x}, {scroll_y}), delta={delta}"
        else:
            result["message"] = "Scroll failed"
        
        return result
    
    async def _execute_analyze(self, client_id: str, data: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        screenshot_result = await self.client_manager.send_screenshot_request(client_id)
        if not screenshot_result:
            result["message"] = "Screenshot failed"
            return result
        
        image_base64 = screenshot_result.get('image_base64')
        if not image_base64:
            result["message"] = "No image data"
            return result
        
        image_data = base64.b64decode(image_base64)
        elements = self.model_service.analyze_screenshot(image_data)
        
        result["status"] = OperationResult.SUCCESS
        result["message"] = f"Detected {len(elements)} UI elements"
        result["elements"] = [
            {"type": e.elem_type, "text": e.text[:50], "interactivity": e.interactivity}
            for e in elements[:20]
        ]
        
        return result
    
    async def _execute_sequence(self, client_id: str, data: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        steps = data.get("steps", data.get("步骤", []))
        
        if not steps:
            result["message"] = "Missing operation steps"
            return result
        
        self.model_service.clear_debug_steps()
        step_results = []
        
        for i, step in enumerate(steps):
            step_result = await self.process_command(step)
            step_results.append(step_result)
            
            if step_result["status"] != OperationResult.SUCCESS:
                result["message"] = f"Step {i+1} failed: {step_result['message']}"
                result["step_results"] = step_results
                return result
            
            await asyncio.sleep(step.get("delay", step.get("延迟", 0.5)))
        
        result["status"] = OperationResult.SUCCESS
        result["message"] = f"Sequence completed, {len(steps)} steps"
        result["step_results"] = step_results
        
        return result
    
    async def _execute_hotkey(self, client_id: str, data: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        keys = data.get("keys", data.get("按键", []))
        
        if not keys:
            result["message"] = "Missing keys for hotkey"
            return result
        
        hotkey_payload = {
            "operation": "hotkey",
            "x": 0,
            "y": 0,
            "keys": keys if isinstance(keys, list) else [keys]
        }
        
        if await self.client_manager.send_coordinates(client_id, hotkey_payload):
            result["status"] = OperationResult.SUCCESS
            result["message"] = f"Hotkey executed: {keys}"
        else:
            result["message"] = "Hotkey failed"
        
        return result
    
    async def _execute_clear_and_type(self, client_id: str, data: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        text = data.get("text", data.get("文本", ""))
        
        if not text:
            result["message"] = "Missing text"
            return result
        
        type_payload = {
            "operation": "clear_and_type",
            "x": 0,
            "y": 0,
            "text": text
        }
        
        if await self.client_manager.send_coordinates(client_id, type_payload):
            result["status"] = OperationResult.SUCCESS
            result["message"] = f"Clear and type success: {text}"
        else:
            result["message"] = "Clear and type failed"
        
        return result
