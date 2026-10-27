# 知记工作台 · 技术基线

> 版本：v0.2（2026-10-27） · 与 `WORKBENCH_PRODUCT.md` 配套
> 当前进度：阶段 A/B 完成；阶段 C 的 ① 视频解析、② 笔记与待办、③ 知识库、④ 文档助手 MVP 已落地。

## 技术栈

| 层 | 选型 | 当前实现 |
| --- | --- | --- |
| 后端 | **Python 3.12 + FastAPI** | `src/zhiji/web/app.py` 统一挂载原业务、笔记、知识库与文档路由 |
| ORM | **SQLAlchemy 2.x（声明式）** | `src/zhiji/storage/tables.py` 按工作区扩表，仓储层在 `storage/` |
| 数据库 | **SQLite（默认）/ PostgreSQL（可切）** | 本地默认 `data/zhiji.db` |
| 任务队列 | **进程内 `TaskWorker`** | 长任务可查状态、可取消；知识库文本导入与文档单篇协作同步完成，批量解析和 `doc_jobs` 后续接任务队列 |
| 前端 | **Vue 3 + Vite + Pinia** | `frontend/src/`；Bento 首页 + Dot Dock + 焦点工作区模式 |
| 静态托管 | **FastAPI 托管 `frontend/dist/`** | 开发时 Vite `:5173` 反代 FastAPI `:8000`，生产直接由 FastAPI 提供构建产物 |
| 图标 | **lucide-vue-next** | 全站统一线性图标 |
| 转写 | **faster-whisper** | 视频解析与未来媒体工坊共用 |
| 媒体处理 | **FFmpeg**（系统命令行） | 媒体工坊尚未实现 |
| 知识检索 | **中文字符 n-gram + 128 维稳定哈希向量** | 检索与重排在本机完成，向量以 JSON 存入 SQLite |
| 凭据安全 | **AES-GCM 加密入库** | Key / Cookie 只存密文，界面只显掩码 |

## 模块边界

```text
frontend/                         # Vue 3 工作台
├── src/components/AppShell.vue   # Bento 首页 / 焦点模式 / 工作区切换
├── src/workspaces/               # ① Video / ② Notes / ③ Knowledge / ④ Docs
├── src/stores/                   # auth / ui / core
├── src/lib/api.js                # REST + SSE / AbortController
└── DESIGN.md                     # 视觉规范与验收记录

src/zhiji/
├── web/                          # FastAPI 入口与按工作区拆分的路由
│   ├── app.py                    # 原业务 API、SSE、静态资源托管
│   ├── notes_api.py              # ② /api/notes, /api/todos
│   ├── knowledge_api.py          # ③ /api/knowledge/*
│   └── docs_api.py               # ④ /api/docs/*
├── knowledge.py                  # 文本提取、切片、分词、哈希向量与相关性排序
├── platforms/                    # ① 平台适配器
├── llm/                          # OpenAI 兼容客户端
├── writer/                       # Markdown 幂等落盘
├── worker/                       # 长任务执行
├── security/                     # 凭据加解密
└── storage/                      # ORM 表、仓储与用户作用域
    ├── knowledge_repository.py
    ├── note_repository.py
    └── doc_repository.py
```

工作区之间不直接互调。它们共享数据库、用户作用域、LLM 客户端与任务队列；简单 CRUD 和文本导入在请求内完成，耗时解析/媒体任务再进入 `TaskWorker`。

## 当前已实现的工作区

### ① 视频解析
- 原多平台抓取、转写、笔记生成、SSE 对话迁入 Vue 工作区。
- 保留会话抽屉、模型选择、任务状态与设置入口。

### ② 笔记与待办
- 笔记 CRUD、标签、置顶、700ms 自动保存、`Ctrl+S` 手动保存。
- 待办 CRUD、优先级、截止日期、关联笔记、完成进度、两段式删除。
- 当前未实现提醒、日历和习惯打卡，继续保留为后续迭代。

### ③ 知识库
- 资料库与资料 CRUD；TXT、Markdown、CSV、JSON、HTML 导入。
- 按段落/句末边界切片，保留原文偏移，支持命中回跳与高亮。
- 中文双字 n-gram + 哈希向量混合排序，并设置相关性门槛。
- 原文阅读、检索轨道、引用片段、SSE 资料问答与停止生成。
- 资料库、资料、切片全部按 `user_id` 隔离，跨用户统一返回 404。
- PDF / Office 当前返回明确的不支持提示，尚未接入解析器。

### ④ 文档助手
- 单文档 CRUD、标题 / 正文编辑、700ms 自动保存、`Ctrl+S` 立即保存与版本号防竞态。
- 新建文档可直接从笔记或知识库资料复制正文，来源类型和来源 ID 随文档保留。
- 文档列表支持标题 / 正文搜索、字符统计、今日更新与素材数统计。
- 总结 / 问答 / 续写 / 润色通过 SSE 流式返回；请求发送浏览器中的最新草稿，不等待自动保存。
- 润色优先使用编辑器当前选区；AI 结果不会自动覆盖正文，由用户选择插入、替换选区、替换全文或复制。
- Markdown / 纯文本下载由后端生成附件；所有文档、来源查询与导出均按 `user_id` 隔离。
- 当前未实现批处理、PDF / Office 等复杂格式转换，也未引入 `doc_jobs` 异步任务。

## 长任务怎么持久化

- 提交时往 `tasks` 表写一行（`status=PENDING`，`payload_json` 记参数），返回 `task_id`。
- worker 取走 → `status=RUNNING` → 进度回写 `progress`。
- 完成写 `status=SUCCEEDED` + `result_json`；失败写 `status=FAILED` + `error`。
- 页面可关闭，回来按 `task_id` 查状态与产物；进程重启后恢复扫描 RUNNING 任务。
- 媒体工坊、未来的文档批处理和知识库批量/二进制解析走这条队列；当前单文档 SSE 协作不占任务队列。

## 实时怎么推

- 视频问答、知识库资料问答、文档 AI 协作使用 **SSE**。
- 知识库问答与文档协作在前端使用 `AbortController`；客户端断开后服务端取消后台生成线程。
- 长任务进度继续走轮询 `GET /api/tasks/{id}`，不引入 WebSocket。

## 数据放哪

- 结构化数据、笔记全文、知识库原文与切片、文档正文 → SQLite。
- 哈希向量 → `knowledge_chunks.embedding_json`，不上传第三方向量服务。
- 凭据密文 → `llm_credentials` / `platform_credentials`。
- 配置 → `config/zhiji.json` + `app_settings`。
- 未来的上传原件、音视频和导出文档 → `output/` 下按工作区分目录。

## 降级原则

- LLM 不可用时：笔记生成、资料问答与文档 AI 协作停用，但文档 CRUD、自动保存、取材、导出以及知识库导入 / 阅读 / 检索仍可用。
- 平台不可用时：只影响对应平台的视频解析，不影响本地笔记与知识库。
- 未安装 FFmpeg / Whisper 时：只影响媒体工坊或无声视频转写，不阻塞其他工作区。
