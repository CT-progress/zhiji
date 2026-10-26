# 知记工作台 · 数据 / API 草图

> 版本：v0.1（2026-10-26） · 只画大方向，不锁字段类型

## 一、核心数据表（在已有库上叠加）

已有表：`users` `tasks` `conversations` `conversation_messages` `llm_credentials` `platform_credentials` `app_settings`。

新增表（按工作区）：

```
── ② 笔记与待办 ──
notes            id, user_id, source_task_id?, source_url?, title, content_md, tags_json, created_at, updated_at
tasks_todo       id, user_id, title, note_id?, due_at?, status(enum: todo/done/cancelled), priority, created_at, updated_at
reminders        id, user_id, title, remind_at, repeat_rule?, done, fired_at?, created_at
calendar_events  id, user_id, title, starts_at, ends_at, note_id?, location?, created_at
habits           id, user_id, name, cron?, streak_count, created_at
habit_logs       id, habit_id, logged_on(date), created_at   (唯一键 habit_id+logged_on)

── ③ 知识库 ──
kb_projects      id, user_id, name, description, created_at
kb_files         id, user_id, project_id?, stored_path, original_name, mime, size, sha256, parsed(bool), source(enum: zhiji/upload), created_at
kb_chunks        id, file_id, ordinal, text, embedding_blob?(向量本体另存), fts_rowid, created_at
kb_qa_sessions   id, user_id, project_id?, model_name, created_at
kb_qa_turns      id, qa_session_id, role, content, cited_chunk_ids_json, created_at

── ④ 文档助手 ──
doc_jobs         id, user_id, kind(enum: summary/qa/generate/polish/batch/convert), input_ref, input_source(enum: kb/upload/note), params_json, output_path?, task_id?, created_at, finished_at?

── ⑤ 媒体工坊 ──
media_assets     id, user_id, kind(enum: image/audio/video), stored_path, original_name, mime, duration?, width?, height?, created_at
media_jobs       id, user_id, asset_id?, kind(enum: subtitle/ffmpeg/clip/export), params_json, output_path?, task_id?, created_at, finished_at?
```

共用 `tasks` 表跑长任务；不另起队列。`user_id` 全部带，沿用已有用户隔离。

## 二、核心 API 端点（按工作区前缀，已有 `/api/auth` `/api/models` 不再列）

```
── ① 视频解析（已有，沿用） ──
POST /api/tasks              # 提交解析任务（type=note）
GET  /api/tasks/{id}         # 查状态
GET  /api/conversations      # 会话列表
POST /api/conversations/{id}/chat          # 流式问答（SSE）

── ② 笔记与待办 ──
GET    /api/notes            POST /api/notes            GET/PUT/DELETE /api/notes/{id}
GET    /api/todos            POST /api/todos            PUT  /api/todos/{id}      # 标记完成 / 改期
GET    /api/reminders        POST /api/reminders        DELETE /api/reminders/{id}
GET    /api/calendar        POST /api/calendar        DELETE /api/calendar/{id}
GET    /api/habits          POST /api/habits          POST /api/habits/{id}/checkin   GET /api/habits/{id}/logs

── ③ 知识库 ──
GET    /api/kb/projects     POST /api/kb/projects     DELETE /api/kb/projects/{id}
GET    /api/kb/files        POST /api/kb/files        # 上传或从笔记导入
DELETE /api/kb/files/{id}    POST /api/kb/files/{id}/reparse   # 重新解析/重建索引
GET    /api/kb/search?q=    # 全文 + 向量混合检索，返回命中文块
POST   /api/kb/qa           # 基于资料的问答（SSE 流式，带引用）

── ④ 文档助手 ──
POST   /api/docs/jobs       # kind 决定动作：summary/qa/generate/polish/batch/convert
GET    /api/docs/jobs/{id}  # 查结果
GET    /api/docs/jobs/{id}/download   # 导出产物

── ⑤ 媒体工坊 ──
POST   /api/media/assets    # 上传
GET    /api/media/assets/{id}        # 预览（按 mime 流式返回）
POST   /api/media/jobs      # kind: subtitle/ffmpeg/clip/export
GET    /api/media/jobs/{id}          # 查进度
GET    /api/media/jobs/{id}/download
```

## 三、约定

- 所有端点走已有的鉴权中间件（cookie 或 bearer），数据按 `user_id` 隔离。
- 长任务一律先落 `tasks` 表再返回 `task_id`，前端轮询；流式结果走 SSE。
- 上传产物落 `output/` 下各工作区子目录，库里只存 `stored_path` + `sha256`。
- 向量本体单独存（避免撑大主库），`kb_chunks.embedding_blob` 可空，缺失时该块只参与全文检索。
