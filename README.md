# 知记 zhiji

把抖音、B 站、知乎等平台的知识视频 / 图文，一键整理成结构化 Markdown 笔记的本地工具。提供 Web 工作台（聊天式交互 + 可视化设置）和 CLI 两种使用方式。

## 功能特性

- **三平台链路（已验证可用）**
  - **抖音**：Cookie 登录态 + API 抓取 + Playwright 音频拦截，Whisper 转写
  - **B 站**：SESSDATA 登录态下优先使用官方字幕（中文 / ai-zh 优选）；无字幕时 yt-dlp 下载音频 + Whisper 兜底
  - **知乎**：Cookie 白名单 + x-zse-96 签名，直接走官方 API 拿全文
- **Web 工作台**：粘贴链接 → 流式生成笔记 → 针对笔记继续追问 / 修改；会话本地持久化
- **可视化设置**：LLM 模型管理（增删改、默认模型、连通测试）、保存路径、转写参数、四平台 Cookie 管理（附获取教程）
- **CLI**：`zhiji note` / `zhiji search` / 登录态收割 / 环境检查
- 所有数据（Cookie、配置、笔记、缓存）均保存在本地项目目录，不上传

## 环境要求

- Python ≥ 3.11（Windows 开发验证，其他平台理论可用）
- 一个兼容 OpenAI 接口的 LLM 服务（DeepSeek、阿里百炼等均可）

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,asr]"
playwright install chromium
zhiji check-env    # 验证环境
```

> Whisper 模型由 faster-whisper 在首次转写时自动下载（默认 `small`）。
> 如需指定缓存位置，可复制 `.env.example` 为 `.env` 后修改 `HF_HOME` 等路径。

## 使用

### Web 方式（推荐）

```powershell
zhiji web          # 默认 http://127.0.0.1:8000
```

1. 左下角「设置 → 模型」添加一个 LLM 模型（名称 / API Key / Base URL / 模型 ID）
2. 「设置 → Cookie」按页面提示粘贴各平台 Cookie（B 站需含 `SESSDATA`，知乎需含 `d_c0`，抖音需含 `sessionid`）
3. 回到首页选择平台、粘贴链接，生成笔记后可继续对话修改

### CLI 方式

```powershell
zhiji note <链接>              # 生成笔记
zhiji search <关键词>          # 搜索（默认 bilibili）
zhiji douyin-login             # 浏览器登录并保存抖音登录态
zhiji zhihu-login              # 同上，知乎
zhiji bilibili-login           # 同上，B 站
zhiji models                   # 列出模型配置
zhiji config-show              # 查看当前设置
```

## 项目结构

```
src/zhiji/
├── cli.py               # CLI 入口（typer）
├── pipeline.py          # 抓取 → 转写 → 生成 → 落盘 流水线
├── config.py            # 配置管理（config/zhiji.json）
├── platforms/           # 平台适配器 + 各平台 Cookie 持久化
│   ├── douyin.py / douyin_cookies.py
│   ├── bilibili.py / bilibili_cookies.py
│   └── zhihu.py / zhihu_cookies.py / zse.py（x-zse-96 签名）
├── transcription/       # faster-whisper 转写引擎
├── writers/             # Markdown 笔记落盘
├── llm/                 # OpenAI 兼容客户端 + 提示词
└── web/                 # FastAPI 后端 + 单文件前端（static/config.html）
tests/                   # pytest 测试
```

## 开发

```powershell
pytest               # 运行测试
ruff check src tests # 代码检查
```

## 隐私与安全

- `data/`（平台 Cookie）、`config/zhiji.json`（API Key）、`.env` 均在 `.gitignore` 中，不会进入版本库
- Cookie 仅按平台白名单精简后保存在本机 `data/` 目录

## 路线图

- [x] B 站 / 知乎 / 抖音链路 + Web 工作台
- [ ] 小红书适配器
- [ ] B 站搜索（需 wbi 签名，当前匿名 412）