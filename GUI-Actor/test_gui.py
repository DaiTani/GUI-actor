import sys
import os
import torch
from qwen_vl_utils import process_vision_info
from transformers import Qwen2VLProcessor

script_dir = os.path.dirname(os.path.abspath(__file__))
src_path = os.path.join(script_dir, "src")
sys.path.insert(0, src_path)
print(f"[*] 已挂载模型源码目录: {src_path}")

try:
    # 导入自定义的模型类
    from gui_actor.modeling import Qwen2VLForConditionalGenerationWithPointer
    print("[*] 成功加载自定义模型架构 (Pointer Head Enabled)")
except ImportError as e:
    print(f"[!] 导入失败，请检查是否在 GUI-Actor 根目录下运行: {e}")
    sys.exit(1)

# 2. 模型配置
model_id = "microsoft/GUI-Actor-2B-Qwen2-VL"

def main():
    # 加载处理器
    print(f"[*] 正在初始化处理器: {model_id}")
    processor = Qwen2VLProcessor.from_pretrained(model_id)

    # 加载模型
    print(f"[*] 正在加载模型权重到 GPU (RTX 4070)...")
    # 注意：我们使用 sdpa 替代 flash_attention_2 以确保在未安装编译库时正常运行
    model = Qwen2VLForConditionalGenerationWithPointer.from_pretrained(
        model_id,
        torch_dtype=torch.bfloat16,
        device_map="cuda",
        trust_remote_code=True,
        attn_implementation="sdpa" 
    ).eval()

    # 3. 准备测试数据
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image", 
                    "image": "/mnt/c/Users/24254/Desktop/screen.png"
                },
                {
                    "type": "text", 
                    # 针对你的图片做了修改，让它寻找右下角的“开始发酵”按钮
                    "text": "Identify the blue '开始发酵' button and provide its location."
                },
            ],
        }
    ]

    # 4. 执行推理流程
    print("[*] 正在处理图像和文本输入...")
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    image_inputs, _ = process_vision_info(messages)
    
    inputs = processor(
        text=[text], 
        images=image_inputs, 
        return_tensors="pt"
    ).to("cuda")

    print("[*] 模型正在思考中...")
    with torch.no_grad():
        try:
            outputs = model.generate(
                **inputs, 
                max_new_tokens=128,
                do_sample=False,        
                repetition_penalty=1.5, 
                return_dict_in_generate=True, 
                # 显式清除引起 Warning 的参数
                temperature=None,
                top_p=None,
                top_k=None,
                pad_token_id=processor.tokenizer.pad_token_id,
                eos_token_id=processor.tokenizer.eos_token_id,
            )
            
            # 获取生成的文本并正确解码
            generated_ids = outputs.sequences[:, inputs.input_ids.shape[1]:]
            decoded_results = processor.batch_decode(generated_ids, skip_special_tokens=True)
            response = decoded_results[0].strip() if decoded_results else "模型未生成任何内容"

            print("\n" + "="*40)
            print("💡 模型推理结果:")
            print(response)
            print("="*40 + "\n")

            # 如果模型输出了类似 {"point": [y, x]}，尝试解析它
            if "[" in response and "]" in response:
                print("🎯 检测到坐标标记！")
            elif "point" in response.lower():
                print("🎯 模型提到了坐标，请检查输出文本。")
                
        except Exception as e:
            print(f"❌ 推理过程中出现错误: {e}")

if __name__ == "__main__":
    main()