import os
import sys
import time
import base64
import tempfile
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any

SCRIPT_DIR = Path(__file__).parent.parent.resolve()
GUI_ACTOR_DIR = SCRIPT_DIR / "GUI-Actor"
sys.path.insert(0, str(GUI_ACTOR_DIR / "src"))
sys.path.insert(0, str(SCRIPT_DIR / "OmniParser"))

import torch
import cv2
import numpy as np

from gui_actor.modeling import Qwen2VLForConditionalGenerationWithPointer
from gui_actor.inference import inference
from gui_actor.constants import grounding_system_message
from transformers import Qwen2VLProcessor
from util.omniparser import Omniparser

import sys
sys.path.insert(0, str(SCRIPT_DIR / "shared"))
from constants import INPUT_KEYWORDS, BUTTON_KEYWORDS, STOPWORDS
from protocol import UIElement, WindowInfo
from utils import setup_logger


class ModelService:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = setup_logger('ModelService', config.get('logging'))
        
        self.processor: Optional[Qwen2VLProcessor] = None
        self.gui_model = None
        self.omni_parser = None
        
        self.model_config = config.get('model', {})
        self.omniparser_config = self.model_config.get('omniparser', {})
        self.enable_omniparser = self.model_config.get('enable_omniparser', True)
        
        self.tmp_dir = SCRIPT_DIR / "tmp_Image"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        
        self.screen_width = 1920
        self.screen_height = 1080
        self.current_monitor_index = 0
        self.monitors_info: List[Dict[str, Any]] = []
        self.target_window = None
        self.target_window_rect: Optional[Dict[str, int]] = None
        self.debug_steps: List[Dict[str, Any]] = []
    
    def initialize(self) -> bool:
        try:
            self.logger.info("Loading GUI-Actor model...")
            model_name = self.model_config.get('gui_actor', 'microsoft/GUI-Actor-2B-Qwen2-VL')
            self.processor = Qwen2VLProcessor.from_pretrained(model_name)
            self.gui_model = Qwen2VLForConditionalGenerationWithPointer.from_pretrained(
                model_name, torch_dtype=torch.bfloat16, device_map="cuda",
                trust_remote_code=True, attn_implementation="sdpa"
            ).eval()
            
            if self.enable_omniparser:
                self.logger.info("Loading OmniParser model...")
                omniparser_config = {
                    'som_model_path': self.omniparser_config.get(
                        'som_model_path', 
                        str(SCRIPT_DIR / "OmniParser" / "weights" / "icon_detect" / "model.pt")
                    ),
                    'caption_model_name': 'florence2',
                    'caption_model_path': self.omniparser_config.get(
                        'caption_model_path',
                        str(SCRIPT_DIR / "OmniParser" / "weights" / "icon_caption_florence")
                    ),
                    'BOX_TRESHOLD': self.omniparser_config.get('box_threshold', 0.05)
                }
                self.omni_parser = Omniparser(omniparser_config)
            
            self.logger.info("Warming up model...")
            self._warmup()
            self.logger.info("Model service initialized successfully!")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize model service: {e}")
            return False
    
    def _warmup(self):
        try:
            test_img = np.zeros((100, 100, 3), dtype=np.uint8)
            test_path = str(self.tmp_dir / "warmup.png")
            cv2.imwrite(test_path, test_img)
            
            conversation = [
                {"role": "system", "content": [{"type": "text", "text": grounding_system_message}]},
                {"role": "user", "content": [
                    {"type": "image", "image": f"file://{test_path}"},
                    {"type": "text", "text": "test"}
                ]}
            ]
            
            _ = inference(
                conversation=conversation, model=self.gui_model, tokenizer=self.processor.tokenizer,
                data_processor=self.processor, use_placeholder=True, topk=5
            )
            
            os.remove(test_path)
        except Exception as e:
            self.logger.warning(f"Warmup failed: {e}")
    
    def set_window_info(self, window_info: WindowInfo):
        self.screen_width = window_info.width
        self.screen_height = window_info.height
        self.target_window = window_info.target_window_handle
        self.target_window_rect = window_info.target_window_rect
    
    def parse_ui_elements(self, image_path: str) -> List[UIElement]:
        if not self.omni_parser:
            return []
        
        with open(image_path, "rb") as f:
            image_base64 = base64.b64encode(f.read()).decode()
        
        _, parsed_content_list = self.omni_parser.parse(image_base64)
        
        elements = []
        for item in parsed_content_list:
            if isinstance(item, dict):
                elem = UIElement(
                    elem_type=item.get('type', 'unknown'),
                    text=item.get('content', ''),
                    bbox=item.get('bbox', None),
                    interactivity=item.get('interactivity', False),
                    confidence=item.get('confidence', 1.0)
                )
                elements.append(elem)
        return elements
    
    def find_element_by_text(self, elements: List[UIElement], target_text: str, 
                             fuzzy: bool = True) -> Optional[UIElement]:
        elem, _ = self.find_element_by_text_with_score(elements, target_text, fuzzy)
        return elem
    
    def find_element_by_text_with_score(self, elements: List[UIElement], target_text: str,
                                        fuzzy: bool = True) -> Tuple[Optional[UIElement], int]:
        target_lower = target_text.lower()
        keywords = [w for w in target_lower.split() if len(w) > 2 and w not in STOPWORDS]
        
        best_match = None
        best_score = 0
        
        for elem in elements:
            elem_text_lower = elem.text.lower()
            score = 0
            
            if elem.interactivity:
                score += 15
            
            if fuzzy:
                if target_lower in elem_text_lower or elem_text_lower in target_lower:
                    score += 100
                else:
                    for kw in keywords:
                        if kw in elem_text_lower:
                            score += 10
                    for word in elem_text_lower.split():
                        if word in target_lower and len(word) > 2:
                            score += 5
                    
                    for ikw in INPUT_KEYWORDS:
                        if ikw in target_lower and ikw in elem_text_lower:
                            score += 20
                    for bkw in BUTTON_KEYWORDS:
                        if bkw in target_lower and bkw in elem_text_lower:
                            score += 20
                    
                    for kw in keywords:
                        if kw in elem_text_lower or elem_text_lower in kw:
                            score += 15
            else:
                if target_lower == elem_text_lower:
                    score = 100
            
            if score > best_score:
                best_score = score
                best_match = elem
        
        return best_match, best_score
    
    def find_element_by_type(self, elements: List[UIElement], target_type: str) -> List[UIElement]:
        target_lower = target_type.lower()
        return [e for e in elements if target_lower in e.elem_type.lower()]
    
    def locate_target(self, prompt: str, image_data: bytes, 
                      window_info: Optional[WindowInfo] = None) -> Optional[Tuple[int, int]]:
        self.logger.info(f"Locating target: {prompt}")
        
        if window_info:
            self.set_window_info(window_info)
        
        t0 = time.time()
        
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False, dir=str(self.tmp_dir)) as f:
            f.write(image_data)
            img_path = f.name
        
        try:
            img = cv2.imread(img_path)
            if img is None:
                self.logger.error(f"Failed to read image")
                return None
            
            img_h, img_w = img.shape[:2]
            
            crop_region = None
            omni_score = 0
            
            if self.omni_parser and self.enable_omniparser:
                self.logger.info("Step 1: OmniParser detecting UI elements...")
                elements = self.parse_ui_elements(img_path)
                matched_elem, omni_score = self.find_element_by_text_with_score(elements, prompt)
                
                if matched_elem and matched_elem.bbox and omni_score >= 30:
                    bbox = matched_elem.bbox
                    crop_x1 = int(bbox[0] * img_w)
                    crop_y1 = int(bbox[1] * img_h)
                    crop_x2 = int(bbox[2] * img_w)
                    crop_y2 = int(bbox[3] * img_h)
                    margin = 20
                    crop_x1 = max(0, crop_x1 - margin)
                    crop_y1 = max(0, crop_y1 - margin)
                    crop_x2 = min(img_w, crop_x2 + margin)
                    crop_y2 = min(img_h, crop_y2 + margin)
                    crop_region = (crop_x1, crop_y1, crop_x2, crop_y2)
                    self.logger.info(f"OmniParser found: '{matched_elem.text}' at bbox {bbox}, score={omni_score}")
                else:
                    self.logger.info(f"OmniParser: No good match (best score={omni_score}), using full image")
            
            if crop_region:
                crop_img = img[crop_region[1]:crop_region[3], crop_region[0]:crop_region[2]]
                crop_path = img_path.replace(".png", "_crop.png")
                cv2.imwrite(crop_path, crop_img)
                inference_img_path = crop_path
                crop_w = crop_region[2] - crop_region[0]
                crop_h = crop_region[3] - crop_region[1]
            else:
                inference_img_path = img_path
                crop_w = img_w
                crop_h = img_h
            
            conversation = [
                {"role": "system", "content": [{"type": "text", "text": grounding_system_message}]},
                {"role": "user", "content": [
                    {"type": "image", "image": f"file://{inference_img_path}"},
                    {"type": "text", "text": prompt}
                ]}
            ]
            
            t2 = time.time()
            self.logger.info("Step 2: GUI-Actor precise localization...")
            pred = inference(
                conversation=conversation, model=self.gui_model, tokenizer=self.processor.tokenizer,
                data_processor=self.processor, use_placeholder=True, topk=5
            )
            t3 = time.time()
            self.logger.info(f"GUI-Actor inference: {t3-t2:.2f}s")
            
            if crop_region and inference_img_path != img_path:
                os.remove(inference_img_path)
            
            if pred["topk_points"] and len(pred["topk_points"]) > 0:
                point = pred["topk_points"][0]
                confidence = pred["topk_values"][0] if pred["topk_values"] else None
                
                if crop_region:
                    px_on_img = int(point[0] * crop_w) + crop_region[0]
                    py_on_img = int(point[1] * crop_h) + crop_region[1]
                else:
                    px_on_img = int(point[0] * img_w)
                    py_on_img = int(point[1] * img_h)
                
                if window_info:
                    offset_x = window_info.offset_x
                    offset_y = window_info.offset_y
                    dpi_scale = window_info.dpi_scale
                    orig_w = window_info.orig_width
                    orig_h = window_info.orig_height
                else:
                    offset_x = 0
                    offset_y = 0
                    dpi_scale = 1.0
                    orig_w = img_w
                    orig_h = img_h
                
                real_x = int((px_on_img / img_w) * orig_w) + offset_x
                real_y = int((py_on_img / img_h) * orig_h) + offset_y
                
                logical_x = int(real_x / dpi_scale)
                logical_y = int(real_y / dpi_scale)
                
                self.debug_steps.append({
                    'px': px_on_img,
                    'py': py_on_img,
                    'real_x': real_x,
                    'real_y': real_y,
                    'logical_x': logical_x,
                    'logical_y': logical_y,
                    'info': prompt[:30]
                })
                
                cv2.circle(img, (px_on_img, py_on_img), 30, (0, 0, 255), 3)
                cv2.putText(img, f"({real_x},{real_y})", (px_on_img + 35, py_on_img + 5), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                if crop_region:
                    cv2.rectangle(img, (crop_region[0], crop_region[1]), 
                                 (crop_region[2], crop_region[3]), (0, 255, 0), 2)
                
                debug_path = str(self.tmp_dir / "debug_target.png")
                cv2.imwrite(debug_path, img)
                
                self.logger.info(f"GUI-Actor normalized: ({point[0]:.4f}, {point[1]:.4f})")
                self.logger.info(f"On scaled image: ({px_on_img}, {py_on_img})")
                self.logger.info(f"DPI scale: {dpi_scale}")
                self.logger.info(f"Physical coords: ({real_x}, {real_y})")
                self.logger.info(f"Logical coords: ({logical_x}, {logical_y})")
                self.logger.info(f"Total: {time.time()-t0:.2f}s")
                
                return (logical_x, logical_y)
            
            self.logger.warning("Target not found")
            return None
            
        finally:
            if os.path.exists(img_path):
                os.remove(img_path)
    
    def analyze_screenshot(self, image_data: bytes) -> List[UIElement]:
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False, dir=str(self.tmp_dir)) as f:
            f.write(image_data)
            img_path = f.name
        
        try:
            elements = self.parse_ui_elements(img_path)
            return elements
        finally:
            if os.path.exists(img_path):
                os.remove(img_path)
    
    def clear_debug_steps(self):
        self.debug_steps = []
    
    def get_debug_steps(self) -> List[Dict[str, Any]]:
        return self.debug_steps
