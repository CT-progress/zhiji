# 演示指南（3 分钟录屏脚本）

本指南描述一段 3 分钟的演示流程，用于展示知记从启动到生成笔记的完整闭环。

## 前置准备

- Python 3.11+ 已安装
- 至少一个 LLM API Key（如 DeepSeek / OpenAI）
- 抖音 / B 站 Cookie（可选，演示时用一个平台即可）

## 演示流程

### 0:00-0:20 启动服务

```powershell
.\scripts\quickstart.bat
```

或手动：

```powershell
.\.venv\Scripts\Activate.ps1
python -m zhiji web --port 8000
```

浏览器打开 http://127.0.0.1:8000。

### 0:20-0:50 注册账号 + 配置 LLM

1. 点击「注册」，输入用户名和密码
2. 左下角「设置 → 模型」→ 添加模型（名称 / API Key / Base URL / 模型 ID）
3. 可选：设置 → Cookie 粘贴平台 Cookie

### 0:50-2:30 生成笔记

1. 回到首页，选择平台（如 B 站）
2. 粘贴一个视频链接
3. 点击生成，观察 SSE 实时进度（抓取 → 转写 → LLM 加工 → 落盘）
4. 笔记生成完毕后，在右侧继续追问 / 修改

### 2:30-3:00 验证持久化

1. 刷新页面，会话和笔记仍在
2. 打开 `output/` 目录查看生成的 Markdown 文件
3. 如果配置了 Obsidian vault 目录，在 vault 中查看同步的笔记

## 冒烟测试

```powershell
# 先启动服务，然后另开终端
python scripts\smoke_test.py
```

冒烟脚本覆盖：健康检查 → 注册 → 登录 → 鉴权访问 → 模型/平台列表 → 会话列表 → 登出。

## 录屏建议

- 用 OBS / Windows Game Bar（Win+G）录制
- 分辨率 1920x1080，30fps
- 重点展示：实时进度条、笔记生成结果、对话追问
