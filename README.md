# GUI Actor - 分布式 GUI 自动化控制系统

基于视觉语言模型的 GUI 自动化控制系统，支持分布式部署，实现远程控制和自动化操作。

## 项目架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              通信架构                                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌──────────────┐      ┌──────────────┐      ┌──────────────┐             │
│   │  核心服务器   │ ───▶ │   服务端模块  │ ◀──▶ │  客户端模块   │             │
│   │ (外部系统)   │      │  (GPU主机)   │      │ (执行终端)   │             │
│   └──────────────┘      └──────────────┘      └──────────────┘             │
│         │                      │                      │                     │
│         │ HTTP REST API        │ WebSocket            │ pyautogui          │
│         │                      │                      │                     │
│         └──────────────────────┴──────────────────────┘                     │
│                                │                                            │
│                        操作完成通知                                          │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 目录结构

```
GUI-actor/
├── server/                          # 服务端模块
│   ├── __init__.py
│   ├── main.py                      # 服务端入口
│   ├── core_server_connector.py     # 核心服务器连接器 (HTTP API)
│   ├── client_manager.py            # 客户端连接管理 (WebSocket)
│   ├── model_service.py             # 模型调用服务 (GUI-Actor + OmniParser)
│   └── command_processor.py         # 指令处理器
│
├── client/                          # 客户端模块
│   ├── __init__.py
│   ├── main.py                      # 客户端入口
│   ├── server_connector.py          # 服务端连接器 (WebSocket)
│   ├── screenshot_service.py        # 截图服务
│   ├── operation_executor.py        # 操作执行器 (pyautogui)
│   └── window_selector.py           # 窗口选择界面
│
├── shared/                          # 共享模块
│   ├── __init__.py
│   ├── protocol.py                  # 通信协议定义
│   ├── crypto.py                    # AES-256-GCM 加密
│   ├── constants.py                 # 常量定义
│   └── utils.py                     # 工具函数
│
├── GUI-Actor/                       # GUI-Actor 模型
├── OmniParser/                      # OmniParser 模型
├── tmp_Image/                       # 临时图片目录
├── requirements.txt                 # 依赖列表
└── README.md                        # 本文档
```

## 环境要求

### 服务端
- Python 3.10+
- CUDA 11.8+ (GPU 推理)
- NVIDIA GPU (推荐 RTX 3070 或更高)
- 内存: 16GB+

### 客户端
- Python 3.10+
- Windows 10/11
- PowerShell 5.1+

## 安装

### 1. 克隆仓库

```bash
git clone https://github.com/DaiTani/GUI-actor.git
cd GUI-actor
git checkout server
```

### 2. 创建 Conda 环境

```bash
conda create -n gui_actor python=3.10
conda activate gui_actor
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 下载模型权重

**GUI-Actor**:
```bash
# 模型会自动从 HuggingFace 下载
# 或手动下载到 GUI-Actor/ 目录
```

**OmniParser**:
```bash
# 下载权重到 OmniParser/weights/ 目录
OmniParser/
├── weights/
│   ├── icon_detect/
│   │   └── model.pt
│   └── icon_caption_florence/
│       └── ...
```

## 部署

### 分布式部署（推荐）

#### Linux 服务端（GPU 主机）

```bash
# 仅复制服务端所需文件
scp -r server/ shared/ GUI-Actor/ OmniParser/ requirements.txt user@server:/path/to/gui-actor/

# SSH 到服务器
ssh user@server

# 激活环境
conda activate gui_actor

# 启动服务端（监听所有网络接口）
python -m server.main --host 0.0.0.0 --port 8080
```

#### Windows 客户端（执行终端）

```bash
# 激活环境
conda activate gui_actor

# 连接 Linux 服务端
python -m client.main --server-host <Linux服务器IP> --server-port 8081
```

### 本地部署（单机测试）

**终端 1 - 启动服务端**:
```bash
conda activate gui_actor
python -m server.main --port 8080
```

**终端 2 - 启动客户端**:
```bash
conda activate gui_actor
python -m client.main --server-host localhost --server-port 8081
```

## 使用说明

### 客户端窗口选择

首次运行客户端时，会显示窗口选择界面：

```
============================================================
Available Windows:
------------------------------------------------------------
   0. [msedge         ] Google Chrome (1920x1080)
   1. [explorer       ] File Explorer (800x600)
   2. [notepad        ] Untitled - Notepad (640x480)
------------------------------------------------------------
  'r' - Refresh window list
  'c' - Clear target window (use full screen)
  'q' - Skip window selection
============================================================
Select target window (number/r/c/q):
```

- 输入数字选择目标窗口
- `r` 刷新窗口列表
- `c` 使用全屏模式
- `q` 跳过选择

### API 接口

服务端提供 HTTP REST API：

#### 执行控制指令

```bash
POST /ferment/control
Content-Type: application/json
```

**请求示例**:

```json
{
  "action": "click",
  "target": "开始按钮"
}
```

**支持的指令类型**:

| 指令 | 说明 | 参数 |
|------|------|------|
| `click` | 点击目标 | `target`: 目标描述, `button`: 按键(left/right/middle), `clicks`: 点击次数 |
| `type` | 输入文本 | `target`: 输入框描述, `text`: 输入内容 |
| `move` | 移动鼠标 | `target`: 目标描述 |
| `drag` | 拖拽操作 | `startX`, `startY`, `endX`, `endY` 或 `dragTarget` |
| `scroll` | 滚动操作 | `x`, `y`, `delta`: 滚动量 |
| `hotkey` | 快捷键 | `keys`: 按键列表 |
| `analyze` | 分析界面 | 无额外参数 |
| `sequence` | 组合操作 | `steps`: 操作步骤列表 |

**组合操作示例**:

```json
{
  "action": "sequence",
  "steps": [
    {"action": "click", "target": "用户名输入框"},
    {"action": "type", "text": "admin"},
    {"action": "click", "target": "密码输入框"},
    {"action": "type", "text": "password123"},
    {"action": "click", "target": "登录按钮"}
  ],
  "delay": 0.3
}
```

**响应示例**:

```json
{
  "status": "success",
  "message": "Click success: (500, 300)",
  "coordinates": [500, 300],
  "timestamp": "2026-03-16T22:00:00.000000"
}
```

#### 其他接口

```bash
# 获取系统状态
GET /ferment/status

# 获取窗口列表
GET /ferment/window/list

# 选择目标窗口
POST /ferment/window/select
{"handle": 123456}

# 清除目标窗口
POST /ferment/window/clear
```

### cURL 示例

```bash
# 点击操作
curl -X POST http://localhost:8080/ferment/control \
  -H "Content-Type: application/json" \
  -d '{"action":"click", "target":"开始按钮"}'

# 输入操作
curl -X POST http://localhost:8080/ferment/control \
  -H "Content-Type: application/json" \
  -d '{"action":"type", "target":"搜索框", "text":"Hello World"}'

# 组合操作
curl -X POST http://localhost:8080/ferment/control \
  -H "Content-Type: application/json" \
  -d '{"action":"sequence", "steps":[{"action":"click", "target":"按钮1"}, {"action":"click", "target":"按钮2"}], "delay":0.5}'
```

## 通信协议

### WebSocket 消息类型

| 类型 | 方向 | 说明 |
|------|------|------|
| `screenshot_request` | 服务端→客户端 | 请求截图 |
| `screenshot_response` | 客户端→服务端 | 返回截图 |
| `coordinate_command` | 服务端→客户端 | 坐标指令 |
| `operation_result` | 客户端→服务端 | 执行结果 |
| `heartbeat` | 双向 | 心跳检测 |
| `register` | 客户端→服务端 | 客户端注册 |

### 通信流程

```
1. 核心服务器 -> 服务端: 发送控制指令 (HTTP POST)
2. 服务端 -> 客户端: 发送截图请求 (WebSocket)
3. 客户端: 执行截图，收集窗口信息
4. 客户端 -> 服务端: 发送截图和窗口信息 (WebSocket)
5. 服务端: 模型分析，计算坐标
6. 服务端 -> 客户端: 发送坐标指令 (WebSocket)
7. 客户端: 执行操作
8. 客户端 -> 服务端: 发送执行结果 (WebSocket)
```

## 配置

### 超时配置 (shared/constants.py)

```python
class TimeoutConfig:
    HTTP_REQUEST: float = 30.0        # HTTP 请求超时
    SCREENSHOT_REQUEST: float = 10.0  # 截图请求超时
    SCREENSHOT_EXECUTION: float = 10.0 # 截图执行超时
    MODEL_ANALYSIS: float = 120.0     # 模型分析超时
    OPERATION_EXECUTION: float = 5.0  # 操作执行超时
```

### 服务端配置

```bash
python -m server.main --help

选项:
  --config    配置文件路径
  --port      HTTP 端口 (默认: 8080)
  --host      监听地址 (默认: 0.0.0.0)
```

### 客户端配置

```bash
python -m client.main --help

选项:
  --config           配置文件路径
  --server-host      服务端地址
  --server-port      服务端 WebSocket 端口 (默认: 8081)
  --no-window-select 跳过窗口选择
```

## 性能指标

| 指标 | 目标值 |
|------|--------|
| 坐标误差 | ≤ 2 像素 |
| 操作响应时间 | ≤ 300ms |
| 截图传输时间 | ≤ 2s |
| 模型分析时间 | ≤ 5s |

## 故障排查

### 常见问题

**1. 客户端无法连接服务端**
- 检查服务端是否启动
- 检查防火墙是否开放端口 (8080, 8081)
- 检查网络连通性

**2. 模型加载失败**
- 检查 CUDA 是否可用: `python -c "import torch; print(torch.cuda.is_available())"`
- 检查模型权重是否存在

**3. 截图失败**
- 检查目标窗口是否最小化
- 检查 PowerShell 执行权限

**4. 操作执行失败**
- 检查 pyautogui 权限
- 检查目标窗口是否在前台

### 日志查看

```bash
# 服务端日志
tail -f logs/server.log

# 客户端日志
tail -f logs/client.log
```

## 安全说明

- 传输层支持 TLS 1.3 加密
- 应用层支持 AES-256-GCM 加密
- 建议在生产环境中配置密钥认证

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request。
