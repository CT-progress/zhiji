# 知记工作台 · 数据 / API 现状

> 版本：v0.2（2026-10-27）
> 本文记录**已经落地**的表和接口；后续工作区另标「计划中」。

## 一、数据表

已有基础表：`users` `tasks` `conversations` `conversation_messages` `llm_credentials` `platform_credentials` `app_settings`。

```text
── ② 笔记与待办（已实现） ──
notes       id, user_id, title, content_md, tags_json, pinned, created_at, updated_at
todos       id, user_id, title, note_id?, priority, due_at?, done, created_at, updated_at

── ③ 知识库（已实现） ──
knowledge_libraries
  id, user_id, name, description, color, created_at, updated_at

knowledge_documents
  id, library_id, user_id, title, filename, mime_type, size,
  content_text, content_markdown, status, error, created_at, updated_at

knowledge_chunks
  id, document_id, user_id, ordinal, text, embedding_json,
  char_count, start_offset, end_offset, created_at
```

约定：

- `knowledge_libraries → knowledge_documents → knowledge_chunks` 为级联删除。
- 文档原文、切片文本和 128 维哈希向量均存本机数据库。
- `start_offset / end_offset` 指向规范化原文，用于检索命中回跳。
- 所有业务行都带 `user_id`；用户作用域之外的资料统一视为不存在。

### 计划中

```text
reminders       # 提醒
calendar_events # 日历
habits          # 习惯
habit_logs      # 打卡记录
doc_jobs        # ④ 文档助手
media_assets    # ⑤ 媒体素材
media_jobs      # ⑤ 媒体任务
```

## 二、核心 API

### ① 视频解析（已有）

```text
POST /api/tasks
GET  /api/tasks/{id}
GET  /api/conversations
POST /api/conversations/{id}/chat          # SSE
```

### ② 笔记与待办（已实现）

```text
GET    /api/notes
POST   /api/notes
GET    /api/notes/{id}
PUT    /api/notes/{id}
DELETE /api/notes/{id}

GET    /api/todos
POST   /api/todos
PUT    /api/todos/{id}
DELETE /api/todos/{id}
```

### ③ 知识库（已实现）

```text
GET    /api/knowledge/stats
GET    /api/knowledge/libraries
POST   /api/knowledge/libraries
GET    /api/knowledge/libraries/{library_id}
PATCH  /api/knowledge/libraries/{library_id}
DELETE /api/knowledge/libraries/{library_id}

GET    /api/knowledge/documents?library_id=...
POST   /api/knowledge/libraries/{library_id}/documents   # multipart 上传
GET    /api/knowledge/documents/{document_id}
PATCH  /api/knowledge/documents/{document_id}            # 重命名
DELETE /api/knowledge/documents/{document_id}

GET    /api/knowledge/search?q=...&library_id=...&limit=12
POST   /api/knowledge/ask/stream                         # SSE，带引用
```

当前导入支持 `TXT / Markdown / CSV / JSON / HTML`；PDF、Word、PPT、Excel 返回明确的不支持提示。

### ④ / ⑤（计划中）

```text
POST   /api/docs/jobs
GET    /api/docs/jobs/{id}
GET    /api/docs/jobs/{id}/download

POST   /api/media/assets
GET    /api/media/assets/{id}
POST   /api/media/jobs
GET    /api/media/jobs/{id}
GET    /api/media/jobs/{id}/download
```

## 三、接口约定

- 所有端点沿用现有 cookie 鉴权，数据按 `user_id` 隔离。
- 短文本/文件导入当前同步完成；后续批量与二进制解析先落 `tasks` 表再返回 `task_id`。
- 流式结果统一走 SSE；长任务进度沿用任务轮询接口。
- 未来二进制上传产物落 `output/` 下对应工作区目录，库里只存 `stored_path` + `sha256`。
- 不引入外部向量服务：当前哈希向量随切片存在 SQLite 中，检索在本机进程内完成。
