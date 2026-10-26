# 知记工作台 · 技术基线

> 版本：v0.1（2026-10-26） · 与 `WORKBENCH_PRODUCT.md` 配套

## 技术栈

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 后端 | **Python 3.12 + FastAPI** | 已有 `src/zhiji/web/app.py`，继续在单体里叠加工作区路由 |
| ORM | **SQLAlchemy 2.x（声明式）** | 已有 `storage/tables.py`，新工作区按同一套 `Base` 加表 |
| 数据库 | **SQLite（默认）/ PostgreSQL（可切）** | 本地单文件即够；迁移走 SQLAlchemy 迁移 |
| 任务队列 | **进程内 `TaskWorker`（已有）** | 长任务提交后异步执行、可查状态、可回调；持久化在 `tasks` 表 |
| 前端 | **原生 JS + 单页 + FastAPI 静态托管** | 已有 `web/static/app.js / app.css`，工作区按 Tab 切换 |
| 转写 | **faster-whisper**（`transcription/engine.py`） | 本地推理，不调外部 API |
| 媒体处理 | **FFmpeg**（系统命令行） | 字幕生成后裁剪 / 转码 / 片段导出 |
| 检索 | 全文：SQLite FTS5；向量：本地向量库（如 `chromadb` 或自建余弦表） | 第二版可换托管向量服务 |
| 凭据安全 | **AES-GCM 加密入库**（已有 `security/`） | Key / Cookie 只存密文，界面只显掩码 |

## 模块边界

```
src/zhiji/
├── web/            # FastAPI 入口 + 静态前端；按工作区挂路由前缀
├── platforms/      # ① 视频解析：已有平台适配器，不动
├── notes/          # ② 笔记与待办（新增）：notes / tasks / reminders / habits
├── knowledge/      # ③ 知识库（新增）：项目库 / 文件 / 解析 / 索引 / 问答
├── docs/           # ④ 文档助手（新增）：总结 / 问答 / 生成 / 润色 / 批处理 / 导出
├── media/          # ⑤ 媒体工坊（新增）：上传 / 预览 / 字幕 / FFmpeg / 导出
├── transcription/  # Whisper 转写引擎（已有，媒体工坊与视频解析共用）
├── llm/            # LLM 客户端（已有）：所有工作区共用
├── worker/         # 任务编排（已有）：所有长任务共用
├── security/       # 加解密（已有）
└── storage/        # ORM 表与仓储层（已有，逐步扩表）
```

每个工作区是一个**包**，对 Web 层暴露路由、对 worker 层暴露 handler、对 storage 层暴露自己的表与仓储。工作区之间**不直接互调**，只通过共享数据库与共享任务队列耦合。

## 长任务怎么持久化

- 提交时往 `tasks` 表写一行（`status=PENDING`，`payload_json` 记参数），返回 `task_id`。
- worker 取走 → `status=RUNNING` → 进度回写 `progress` 字段。
- 完成写 `status=SUCCEEDED` + `result_json`；失败写 `status=FAILED` + `error`。
- 页面可关：回来按 `task_id` 查状态与产物。**进程重启后 RUNNING 任务需恢复扫描**（已有逻辑兜底）。
- 媒体工坊的 FFmpeg、知识库的批量解析、文档助手的批处理都走这条队列，不另起线程池。

## 实时怎么推

- 短任务（笔记流式生成、问答流式回答）走 **SSE**（已有 `_sse` 辅助与 `/api/chat` 流式端点）。
- 长任务进度走**轮询 `GET /api/tasks/{id}`**（已有），不做 WebSocket，避免引入连接管理复杂度。
- 媒体处理进度也走轮询，由 worker 回写 `progress`。

## 数据放哪

- 结构化数据（笔记、任务、知识库条目、文件元数据、向量索引元数据）→ **SQLite 文件**（`data/zhiji.db`）。
- 二进制产物（上传的原始文件、生成的音视频、转写音频、导出文档）→ **具名文件目录**（`output/` 下按工作区分子目录）。
- 向量本体 → 本地向量库目录；元数据（chunk_id → 文档 id）在 SQLite。
- 凭据密文 → SQLite `llm_credentials` / `platform_credentials` 表（已有）。
- 配置 → `config/zhiji.json`（已有）+ `app_settings` 表（已有）。
