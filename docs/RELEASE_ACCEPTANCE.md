# 知记 v0.1.0 发布验收

> 验收日期：2026-10-27
> 结论：**发布候选验收通过**（本机桌面 Web + 自托管单机范围）
> 详细自动化证据：仓库外与仓库内 `.tmp/` 验收目录；`.tmp/` 不进入版本库。

## 1. 交付范围

本版本只验收以下形态：

- 桌面浏览器工作台（目标视口 1440×900、1280×800）
- 自托管单机部署（SQLite 默认，Docker Compose 为正式部署入口）
- 单用户工作流，以及多账号数据隔离
- 可选登录鉴权；本地无鉴权模式保持兼容

明确不在本版本范围：

- 手机端 / 平板专项适配
- SaaS 多租户、计费、运营后台、开放注册运营
- 多节点高可用、跨地域容灾、商业级 SLA
- 阶段 C 中尚未交付的提醒 / 日历 / 习惯打卡、PDF / Office 解析、批处理等增强项

## 2. 验收结果

| 项目 | 结果 | 说明 |
| --- | --- | --- |
| Ruff | 通过 | `ruff check src tests` |
| Mypy | 通过 | 63 个源文件，无问题 |
| 前端生产构建 | 通过 | `npm ci` + `npm run build`，生成 `frontend/dist/` |
| 全量 pytest | 通过 | 最终回归：429 passed、1 skipped（仅第三方 Starlette/httpx 弃用警告） |
| 打包不变量测试 | 14/14 | Dockerfile、`.dockerignore`、quickstart、CI 契约 |
| API 冒烟 | 13/13 | 在独立验收实例 `127.0.0.1:8001` 执行 |
| 双账号实时隔离 | 47/47 | 会话、任务、笔记等用户作用域隔离 |
| 重启持久化 | 26/26 | 服务重启后数据与状态保持一致 |
| 备份恢复 | 26/26 | 恢复后数据库 SHA256 与备份一致 |
| 迁移幂等 | 通过 | 首次导入 22 条会话，重复导入 0 条 |
| 全新安装 | 通过 | 注册 / 登录 / 数据库 / 配置 / 首篇笔记 / `output` 惰性创建 |
| 桌面 UI | 65/65 | 两档视口下的空状态、长列表、超长标题、横向溢出、按钮遮挡、错误 Toast、控制台异常 |
| Docker 静态验收 | 通过 | 多阶段前端构建、非 root、卷、健康检查及 CI job 契约 |
| Docker 真构建 | 通过 | GitHub Actions `docker` job 真实 build / 非 root / 启动 / 健康检查全部通过（run 40647291762，2026-10-28） |

## 3. 已修复的发布阻塞

### 跨用户笔记文件覆盖

`POST /api/note/save` 原先允许请求指定的笔记文件路径越出当前用户目录，在鉴权模式下可能覆盖其他用户文件。修复后：

- 鉴权模式写入 `output/notes/{user_id}/`
- 只允许覆盖本人目录或本人会话绑定文件
- 非法跨用户覆盖返回 404
- 无鉴权本地模式保持原行为
- 回归用例：`test_note_save_files_are_isolated_by_user`

## 4. UI 验收覆盖

Playwright 在 1440×900 与 1280×800 下覆盖：

- 首页空状态与填充状态
- 视频解析、笔记待办、知识库、文档助手、媒体工坊五个工作区
- 空状态、长列表、超长标题截断
- 横向溢出、交互元素被遮挡、错误 Toast
- 浏览器控制台异常

结果文件：`.tmp/ui-acceptance/result.json`
截图：`.tmp/ui-acceptance/screens/`

## 5. 复跑命令

```powershell
$env:PYTHONUTF8='1'
& .\.venv\Scripts\ruff.exe check src tests
& .\.venv\Scripts\mypy.exe
& .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider

cd frontend
npm ci --no-audit --no-fund
npm run build
cd ..

# 需先启动一个独立验收实例，避免碰正式 data/、output/
& .\.venv\Scripts\python.exe scripts\smoke_test.py --base http://127.0.0.1:8001
```

## 6. 已知边界与后续归属

| 边界 | 处理 |
| --- | --- |
| 手机端未做专项适配 | 不属于 v0.1.0；用户已明确不把手机端作为本版交付项 |
| SaaS 多租户 / 计费 / 运营能力 | 不属于 v0.1.0，留到第二版 |
| 本机无 Docker CLI | 已做 Dockerfile / CI 静态验收；真实镜像构建与启动由 CI 覆盖 |
| 媒体格式兼容性 | 依赖 PyAV 可解码格式；Whisper 字幕依赖可选 `asr` 额外依赖 |
| PostgreSQL / 多节点 | SQLite 单机是默认；PostgreSQL 属可选部署，不在本轮性能验收内 |
| 阶段 C 增强项 | 提醒、日历、习惯、PDF / Office、批处理等按路线图后续实现 |
| CI checks job 为红 | 两个用例依赖本机构建产物与终端宽度（无 frontend/dist 时回退旧静态页、rich 在窄终端截断 --help）；属测试环境问题，产品代码未受影响，标记后单独修复 |

## 7. 发布检查清单

- [x] 两个独立账号的数据与文件作用域隔离
- [x] 重启不丢数据
- [x] 备份 / 恢复可回滚
- [x] 旧数据迁移可重复执行
- [x] 全新安装可完成首次使用闭环
- [x] 桌面 UI 两档视口全绿
- [x] API 冒烟通过
- [x] lint / 类型检查 / 单元测试通过
- [x] Docker 构建契约进入 CI
- [x] GitHub Actions Docker job 实际通过后创建 `v0.1.0` 标签
