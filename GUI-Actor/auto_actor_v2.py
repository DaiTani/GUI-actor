import os
import sys
import torch
import subprocess
import cv2
import base64
import json
from io import BytesIO
from PIL import Image

script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(script_dir, "src"))
sys.path.insert(0, os.path.join(script_dir, "..", "OmniParser"))

from gui_actor.modeling import Qwen2VLForConditionalGenerationWithPointer
from gui_actor.inference import inference
from gui_actor.constants import grounding_system_message
from qwen_vl_utils import process_vision_info
from transformers import Qwen2VLProcessor
from util.omniparser import Omniparser

WIN_USER = "24254"
WIN_DESKTOP = f"C:\\Users\\{WIN_USER}\\Desktop"
WSL_DESKTOP = f"/mnt/c/Users/{WIN_USER}/Desktop"
SCREENSHOT_NAME = "current_screen.png"
DEBUG_NAME = "debug_result.png"

OMNIPARSER_WEIGHTS = os.path.join(script_dir, "..", "OmniParser", "weights")
OMNIPARSER_CONFIG = {
    'som_model_path': os.path.join(OMNIPARSER_WEIGHTS, "icon_detect", "model.pt"),
    'caption_model_name': 'florence2',
    'caption_model_path': os.path.join(OMNIPARSER_WEIGHTS, "icon_caption_florence"),
    'BOX_TRESHOLD': 0.05
}

def setup_models():
    print("[*] 正在加载 GUI-Actor 模型...")
    processor = Qwen2VLProcessor.from_pretrained("microsoft/GUI-Actor-2B-Qwen2-VL")
    gui_model = Qwen2VLForConditionalGenerationWithPointer.from_pretrained(
        "microsoft/GUI-Actor-2B-Qwen2-VL", torch_dtype=torch.bfloat16, device_map="cuda",
        trust_remote_code=True, attn_implementation="sdpa"
    ).eval()
    
    print("[*] 正在加载 OmniParser 模型...")
    omni_parser = Omniparser(OMNIPARSER_CONFIG)
    
    return processor, gui_model, omni_parser

def capture_screen():
    win_img = os.path.join(WIN_DESKTOP, SCREENSHOT_NAME)
    wsl_img = os.path.join(WSL_DESKTOP, SCREENSHOT_NAME)
    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms, System.Drawing; "
        "$S = [System.Windows.Forms.Screen]::PrimaryScreen; "
        "$bmp = New-Object System.Drawing.Bitmap($S.Bounds.Width, $S.Bounds.Height); "
        "$g = [System.Drawing.Graphics]::FromImage($bmp); "
        "$g.CopyFromScreen($S.Bounds.Location, [System.Drawing.Point]::Empty, $S.Bounds.Size); "
        f"$bmp.Save('{win_img}', [System.Drawing.Imaging.ImageFormat]::Png); "
        "$g.Dispose(); $bmp.Dispose();"
    )
    subprocess.run(["powershell.exe", "-Command", ps_script], capture_output=True)
    return wsl_img

def parse_ui_elements(omni_parser, image_path):
    with open(image_path, "rb") as f:
        image_base64 = base64.b64encode(f.read()).decode()
    
    _, parsed_content_list = omni_parser.parse(image_base64)
    
    elements = []
    for item in parsed_content_list:
        if isinstance(item, dict):
            elements.append({
                'type': item.get('type', 'unknown'),
                'text': item.get('content', ''),
                'bbox': item.get('bbox', None),
                'interactivity': item.get('interactivity', False)
            })
    return elements

def locate_target(processor, model, image_path, prompt):
    conversation = [
        {"role": "system", "content": [{"type": "text", "text": grounding_system_message}]},
        {"role": "user", "content": [
            {"type": "image", "image": f"file://{image_path}"},
            {"type": "text", "text": prompt}
        ]}
    ]
    
    pred = inference(
        conversation=conversation, model=model, tokenizer=processor.tokenizer,
        data_processor=processor, use_placeholder=True, topk=5
    )
    
    if pred["topk_points"] and len(pred["topk_points"]) > 0:
        return pred["topk_points"][0], pred["topk_values"][0] if pred["topk_values"] else None
    return None, None

def main():
    processor, gui_model, omni_parser = setup_models()
    
    while True:
        choice = input("\n输入 '1' 分析屏幕 | '2' 定位目标 | 'q' 退出: ")
        if choice.lower() == 'q': break
        
        if choice == '1':
            print("[*] 正在截屏...")
            img_path = capture_screen()
            if not os.path.exists(img_path):
                print("❌ 截图失败")
                continue
            
            print("[*] OmniParser 正在解析 UI 元素...")
            elements = parse_ui_elements(omni_parser, img_path)
            
            print(f"\n📊 检测到 {len(elements)} 个 UI 元素:")
            print("-" * 60)
            for i, el in enumerate(elements[:20]):
                type_str = f"[{el['type']}]"
                text_str = el['text'][:30] if el['text'] else ""
                interactive = "✓" if el.get('interactivity') else " "
                print(f"{i+1:2d}. {type_str:15s} {interactive} {text_str}")
            if len(elements) > 20:
                print(f"... 还有 {len(elements) - 20} 个元素")
            
            os.remove(img_path)
            
        elif choice == '2':
            print("[*] 正在截屏...")
            img_path = capture_screen()
            if not os.path.exists(img_path):
                print("❌ 截图失败")
                continue
            
            prompt = input("输入定位指令 (如 '蓝色的开始发酵按钮'): ").strip()
            if not prompt:
                prompt = "开始发酵按钮"
            
            print("[*] GUI-Actor 正在定位...")
            point, confidence = locate_target(processor, gui_model, img_path, prompt)
            
            if point:
                img = cv2.imread(img_path)
                real_h, real_w = img.shape[:2]
                px_x = int(point[0] * real_w)
                px_y = int(point[1] * real_h)
                
                cv2.circle(img, (px_x, px_y), 40, (0, 0, 255), 5)
                cv2.putText(img, "TARGET", (px_x + 50, px_y), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 2)
                
                debug_path_wsl = os.path.join(WSL_DESKTOP, DEBUG_NAME)
                cv2.imwrite(debug_path_wsl, img)
                
                print(f"\n🎯 目标位置: ({px_x}, {px_y})")
                print(f"📊 置信度: {confidence:.4f}" if confidence else "")
                print(f"📷 调试图已保存: {os.path.join(WIN_DESKTOP, DEBUG_NAME)}")
            else:
                print("⚠️ 未检测到目标")
            
            os.remove(img_path)

if __name__ == "__main__":
    main()
