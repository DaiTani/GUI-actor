import os
import sys
import torch
import subprocess
import cv2
import re
import logging
import easyocr
from qwen_vl_utils import process_vision_info
from transformers import Qwen2VLProcessor

# --- 路径与环境配置 ---
WIN_USER = "24254"
WIN_DESKTOP = f"C:\\Users\\{WIN_USER}\\Desktop"
WSL_DESKTOP = f"/mnt/c/Users/{WIN_USER}/Desktop"
SCREENSHOT_NAME = "current_screen.png"
DEBUG_NAME = "debug_result.png"

script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(script_dir, "src"))

from gui_actor.modeling import Qwen2VLForConditionalGenerationWithPointer
from gui_actor.inference import inference
from gui_actor.constants import grounding_system_message

# --- 初始化 ---
def setup_engines():
    print("[*] 正在加载 Qwen2-VL 大脑...")
    model_id = "microsoft/GUI-Actor-2B-Qwen2-VL"
    processor = Qwen2VLProcessor.from_pretrained(model_id)
    model = Qwen2VLForConditionalGenerationWithPointer.from_pretrained(
        model_id, torch_dtype=torch.bfloat16, device_map="cuda",
        trust_remote_code=True, attn_implementation="sdpa"
    ).eval()
    
    print("[*] 正在加载 EasyOCR 眼睛...")
    # 初始化 EasyOCR，指定中文简体 (ch_sim) 和英文 (en)
    # gpu=True 会自动识别你的 RTX 4070
    reader = easyocr.Reader(['ch_sim', 'en'], gpu=True)
    
    return processor, model, reader

def main():
    processor, model, reader = setup_engines()
    wsl_img = os.path.join(WSL_DESKTOP, SCREENSHOT_NAME)
    win_img = os.path.join(WIN_DESKTOP, SCREENSHOT_NAME)
    debug_img_wsl = os.path.join(WSL_DESKTOP, DEBUG_NAME)

    while True:
        choice = input("\n输入 '1' 执行工作流 | 'q' 退出: ")
        if choice.lower() == 'q': break
        if choice != '1': continue

        # 1. 跨系统截图 (PowerShell 脚本)
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
        
        if not os.path.exists(wsl_img):
            print("❌ 截图未生成，请检查路径权限或 Windows 用户名是否正确")
            continue

        final_x, final_y = None, None
        img = cv2.imread(wsl_img)
        if img is None:
            print("❌ 图像加载失败")
            continue
            
        real_h, real_w = img.shape[:2]

        # --- 步骤 1: EasyOCR 文本检索 ---
        print("[*] 阶段 1: 文本解析定位...")
        ocr_results = reader.readtext(wsl_img)
        ocr_target = None
        
        if ocr_results:
            for (bbox, text, prob) in ocr_results:
                # 模糊匹配核心关键词
                if "开始" in text or "发酵" in text:
                    # EasyOCR 坐标格式: [[x0,y0], [x1,y1], [x2,y2], [x3,y3]]
                    # 取对角线平均值作为中心点
                    ocr_x = int((bbox[0][0] + bbox[2][0]) / 2)
                    ocr_y = int((bbox[0][1] + bbox[2][1]) / 2)
                    ocr_target = (ocr_x, ocr_y)
                    print(f"✅ OCR 精准匹配: 【{text}】 (置信度: {prob:.2f}) -> ({ocr_x}, {ocr_y})")
                    break

        # --- 步骤 2: GUI-Actor 视觉推理 ---
        print("[*] 阶段 2: 视觉逻辑核验...")
        prompt = "Locate the large blue button labeled '开始发酵' in the bottom right corner."
        conversation = [
            {"role": "system", "content": [{"type": "text", "text": grounding_system_message}]},
            {"role": "user", "content": [
                {"type": "image", "image": f"file://{wsl_img}"}, 
                {"type": "text", "text": prompt}
            ]}
        ]

        pred = inference(
            conversation=conversation, 
            model=model, 
            tokenizer=processor.tokenizer, 
            data_processor=processor, 
            use_placeholder=True, 
            topk=1
        )

        if pred["topk_points"]:
            best_point = pred["topk_points"][0]
            vl_x = int(best_point[0] * real_w)
            vl_y = int(best_point[1] * real_h)
            print(f"🤖 GUI-Actor 视觉定位: ({vl_x}, {vl_y})")
            
            # --- 最终决策逻辑 ---
            if ocr_target:
                final_x, final_y = ocr_target
                print("💎 决策: 采纳 OCR 物理坐标（最高精度）。")
            else:
                final_x, final_y = vl_x, vl_y
                print("⚖️ 决策: OCR 未命中文字，采纳视觉模型坐标。")

        # --- 3. 验证结果绘制 ---
        if final_x is not None:
            # 绘制醒目的红圈和标签
            cv2.circle(img, (final_x, final_y), 50, (0, 0, 255), 8)
            cv2.putText(img, "AUTO CLICK TARGET", (final_x + 60, final_y), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
            cv2.imwrite(debug_img_wsl, img)
            print(f"🎯 定位成功！坐标: ({final_x}, {final_y})")
            print(f"📸 验证图已保存到桌面: {DEBUG_NAME}")
        else:
            print("⚠️ 未识别到目标元素，请调整软件位置或确保目标可见。")

        # 清理
        if os.path.exists(wsl_img): os.remove(wsl_img)

if __name__ == "__main__":
    main()