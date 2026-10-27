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

── ④ 文档助手（已实现） ──
documents
  id, user_id, title, content, format, source_type, source_id?,
  created_at, updated_at

── ⑤ 媒体工坊（已实现） ──
media_items
  id, user_id?, title, filename, media_type, mime_type, extension, size,
  sha256, duration_ms?, width?, height?, video_codec?, audio_codec?,
  status, error, storage_path, created_at, updated_at

media_artifacts
  id, media_id, user_id?, kind, filename, mime_type, size, storage_path,
  task_id?, metadata_json, created_at
```

约定：

- `knowledge_libraries → knowledge_documents → knowledge_chunks` 为级联删除。
- 文档原文、切片文本和 128 维哈希向量均存本机数据库。
- `start_offset / end_offset` 指向规范化原文，用于检索命中回跳。
- 所有业务行都带 `user_id`；用户作用域之外的资料统一视为不存在。`documents.source_type` 为 `blank / note / knowledge`，取材时复制正文而不是引用素材，后续编辑互不影响。
- `media_items → media_artifacts` 为级联删除；媒体任务复用现有 `tasks` 表，没有单独的 `media_jobs` 表。`tasks.payload_json.media_id` 关联素材，任务类型为 `media.extract_audio / media.transcode / media.trim / media.transcribe`。
- 媒体文件落在 `data/media/<user-scope>/<media-id>/`；数据库只存相对 `storage_path`、SHA-256 与探测元数据。

### 计划中

```text
reminders       # 提醒
calendar_events # 日历
habits          # 习惯
habit_logs      # 打卡记录
doc_jobs        # ④ 文档批处理（未实现）
media_batches   # ⑤ 批量媒体处理（未实现）
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

### ④ 文档助手（已实现）

```text
GET    /api/docs?q=...
POST   /api/docs                              # blank / note / knowledge 取材
GET    /api/docs/{id}
PUT    /api/docs/{id}
DELETE /api/docs/{id}

GET    /api/docs/stats
GET    /api/docs/sources
GET    /api/docs/{id}/export                  # Markdown / text 附件
POST   /api/docs/{id}/assist/stream           # SSE，总结 / 问答 / 续写 / 润色
```

当前为单文档同步闭环；批处理、复杂格式转换和 `POST /api/docs/jobs` 异步任务尚未实现。

### ⑤ 媒体工坊（已实现）

```text
GET    /api/media/stats
GET    /api/media?q=...&media_type=...&offset=0&limit=200
POST   /api/media/upload?filename=...&title=...   # raw body 上传
GET    /api/media/{media_id}
PATCH  /api/media/{media_id}                     # 重命名
DELETE /api/media/{media_id}

GET    /api/media/{media_id}/file
GET    /api/media/{media_id}/download
GET    /api/media/{media_id}/artifacts
GET    /api/media/{media_id}/tasks
POST   /api/media/{media_id}/tasks
POST   /api/media/tasks/{task_id}/cancel

GET    /api/media/artifacts/{artifact_id}/file
GET    /api/media/artifacts/{artifact_id}/download
DELETE /api/media/artifacts/{artifact_id}
```

上传不使用 multipart：文件内容直接作为请求 body，`filename` 与可选 `title` 放 query，默认单文件上限 512 MiB。创建任务 body：

```json
{"action":"extract_audio"}
{"action":"transcode"}
{"action":"trim","start_ms":150,"end_ms":600}
{"action":"transcribe"}
```

动作校验会按素材类型与音轨能力拒绝无效请求；同一素材同时只允许一个活动任务。媒体、任务与产物查询都按当前用户作用域隔离。

## 三、接口约定

- 所有端点沿用现有 cookie 鉴权，数据按 `user_id` 隔离。
- 知识库文本导入与文档 AI 协作当前同步或 SSE 完成；媒体处理走 `tasks` 表异步执行，提交后返回 `task` 并轮询进度。
- 流式结果统一走 SSE；长任务进度沿用任务轮询接口。
- 媒体原件与产物落 `data/media/` 下对应工作区目录，库里只存相对 `storage_path` + `sha256`。
- 不引入外部向量服务：当前哈希向量随切片存在 SQLite 中，检索在本机进程内完成。
