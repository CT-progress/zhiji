# 知记（Zhiji）落地改造计划书

> 版本：v1.2 　编制日期：2026-10-09 　修订：2026-10-13
> 配套文档：`docs/ARCHITECTURE.md`（架构设计，回答"为什么这样设计"）；本文回答"具体怎么做、按什么顺序做"。
> 代码基线：本地 `main`（阶段 0–5 全部完成；按约定暂不推送）
> 修订说明（v1.2）：新增 §1.5「扩展候选」，明确区分**没排期**与**不做**；Obsidian 幂等落盘由“远期”升格为
> **阶段 5.5**；§1.3 补记单机形态的对象存储（MinIO / S3）为非目标；ADR 补 D12。
> 修订说明（v1.1）：产品形态由“对外提供服务的系统”收敛为**自托管的多用户服务**；新增第 7 阶段「作品集交付」；
> 阶段 6 由“照抄接口”改为按责任切分；受众优先级落定为**通用 / 独立开发**；修正阶段总览的过期状态。
> 依据见 §8 的 D8–D12。

---

## 0. 一页速览

| 项 | 内容 |
| --- | --- |
| **一句话目标** | 把“本机能跑的脚本”改造成“**可自托管、多用户、可运维**的知识加工服务”，同时作为“Java 控制面 + Python 数据面”的参考实现 |
| **产品形态** | 自托管优先：一条命令起在自己机器上；**不做对外开放注册的 SaaS**（理由见 §1.1 与 D8） |
| **受众** | **通用 / 独立开发**：自托管形态 + 完整业务闭环优先，Java 控制面保持「可选、最后做」（见 §1.4 与 D11） |
| **总体路线** | 阶段 0 基线冻结 → 阶段 1–5（工程底座与持久化 → 异步任务闭环 → 账号与数据隔离 → 凭据安全 → 容器化部署）→ **阶段 5.5 产物落库（Obsidian）** → 阶段 6 Java 控制面（可选）；另有贯穿全程的阶段 7：作品集交付 |
| **语言策略** | 先用 Python 打通业务闭环；Java 控制面放到最后的可选阶段 |
| **实施原则** | 不重写、只叠加；每一步测试全绿；每一步可独立回滚 |
| **当前状态** | 阶段 0–5 全部完成；阶段 6.1 契约固化完成；本机提交，未推送 |

---

## 1. 目标与范围

### 1.1 产品目标

让知记从“个人脚本”变成一套**可自托管部署、按用户隔离**的知识加工服务——一台机器上 `docker compose up` 就能起来，一台实例可以安全地服务多个账号：

- 任何一台机器上 `docker compose up` 就能跑起来；
- 支持多个用户，各自的数据、密钥、登录态互不可见；
- 提交任务后**异步执行**，可以关掉页面、稍后再看结果；
- 有历史记录、有任务状态、有失败重试；
- 敏感凭据（LLM Key、平台 Cookie）加密存储，绝不落明文。

**形态取舍（v1.1 修订）**：本计划交付的是**自托管形态**，不是“对外开放注册的 SaaS”。

对外开放注册会把项目性质从“工程质量”变成“运维与合规责任”——备案、滥用处理、平台风控转嫁、
Token 成本、on-call；这些投入对 §1.4 的两个目标都几乎不产生增益。自托管形态则相反，它本身就
在展示传统后端能力：配置管理、数据卷、迁移、备份、升级、主密钥轮换。

若将来确要公网开放，**不是改一行配置就能上的**：至少还缺 TLS 终止 + `secure` Cookie、注册闸门
（邀请制 / 关注册）、登录与注册限流、容器重启后遗留 `RUNNING` 任务的恢复。这些分别属于阶段 5.3
（附录，见 §9）与阶段 6 的范围，本计划默认不做。

### 1.2 MVP 边界（本计划要达成的）

| 能力 | MVP 标准 |
| --- | --- |
| 部署 | 单机 Docker Compose 一键启动 |
| 数据库 | SQLAlchemy 抽象；本地 SQLite，生产可切 PostgreSQL |
| 任务 | 提交 → 后台执行 → 查询状态 → 获取产物 |
| 账号 | 注册 / 登录 / 登出；数据按用户隔离 |
| 凭据 | Key 与 Cookie 加密入库，界面只显示掩码 |
| 历史 | 任务与笔记可在 Web 上查询 |
| 质量 | 每个阶段结束时 pytest / ruff / mypy 全绿 |

### 1.3 非目标（本计划明确不做，避免范围失控）

- ❌ Kubernetes、分布式爬虫集群、多机房
- ❌ 知网等学术站点的**全文批量抓取**（合规红线，见 ARCHITECTURE 3.2）
- ❌ 计费、支付、发票
- ❌ 移动端原生 App
- ❌ 微服务拆分（在单体模块化没做透之前）
- ❌ **对外开放注册的 SaaS**（免注册，或任意陌生人自助注册）——会把项目变成运维与合规主体，理由见 §1.1
- ❌ 继续扩张抓取平台数量——4 个平台已足以证明适配器模式可扩展，第 5 个只增加维护成本
- ❌ 单机形态下的对象存储服务（MinIO / S3）——单机用 Docker 具名卷即可，多机阶段再议

---

### 1.4 项目目标与受众

本项目同时服务两个目标，二者**验收方式相反**，因此必须分主次——以**作品集为主、产品为辅**：

| 目标 | 谁在评价 | 怎么算达标 |
| --- | --- | --- |
| 作品集 | 评审者 / 面试官 / 合作方 | 10 分钟看懂架构与取舍：一条命令跑起来、README 讲清楚、`docs/` 有 ADR 与可量化记录 |
| 产品 | 实际使用者 | `docker compose up -d` 后长期稳定，数据可备份、可迁移、可升级 |

**关键认知**：作品集的评价对象是**代码与文档**，不是用户数量。对外开放注册能增加的“用户量”
对作品集几乎没有边际增益，却会引入真实的责任与成本。

**已定（2026-10-13）**：受众优先级为 **通用 / 独立开发**——自托管形态 + 完整业务闭环优先，
Java 控制面保持「可选、最后做」，**不为了展示 Java 而提前冻结 Python 侧**。三个候选方向的取舍如下，
供将来转向时参考：

- 面向 **通用 / 独立开发**（**当前选择**）→ 自托管形态 + 完整闭环，弱化 Java，阶段 6 保持可选；
- 面向 **Java 后端岗** → Java 控制面尽早做，Python 侧停止打磨；
- 面向 **AI / Agent 岗** → 先把 Agent 闭环做扎实，Java 只做薄控制面证明跨语言能力。

**由此确定的顺序**：阶段 5（容器化 + 部署文档，让“一条命令起起来”成立）→ **阶段 5.5（产物落库 / Obsidian 接入）**
→ 阶段 7 的 7.1–7.3（README / 图示 / ADR，贯穿执行）→ 阶段 6（Java 控制面，可选）→ 阶段 7 的 7.4（演示与冒烟脚本收尾）。

---

### 1.5 扩展候选（不进当前阶段，但明确保留）

「**没排期**」与「**不做**」是两件不同的事，必须在文档里分开写。否则读者只能看到
“图上有个组件、计划书里从头到尾不出现”的矛盾——这正是风险 8（没人看得懂）的典型来源。

| 能力 | 状态 | 触发条件 |
| --- | --- | --- |
| **Obsidian 直连 vault（幂等落盘）** | ✅ **已完成**（阶段 5.5） | 稳定 `zhiji_id` + 区段标记幂等 upsert；详见 §5.5 |
| 语义检索 / RAG（pgvector） | ⏸ 保留，待前提 | 阶段 6 引入 PostgreSQL 之后；SQLite 上不存在该能力 |
| 对象存储（MinIO / S3） | ❌ **不做**（见 §1.3） | 仅当进入多机 / 多副本形态 |
| 多步 Agent 编排（检索 → 加工 → 校验） | ⏸ 保留，待前提 | 任务闭环与凭据安全稳定之后 |

**为什么 Obsidian 能升格、另外两个不能**：

- **Obsidian**：产物**已经是带 YAML front-matter 的纯 Markdown**（`src/zhiji/writers/markdown.py`），
  且 `output_dir` 本来就是数据库里的一个配置项（`config.resolve_output_dir()`）；把输出目录指向 vault
  子目录即成立，**不需要插件、不需要额外服务**。真正缺的只有**稳定 ID + 幂等 upsert**——现在的
  `_unique_path()` 会给重复素材生成 `xxx (1).md`，而不是更新原笔记。成本 ≈ 一个迭代。
- **pgvector**：前提是 PostgreSQL，而 PostgreSQL 的引入点在阶段 6；没有它，向量库无处安放。
  在几十条笔记的规模下，它换来的检索能力**没有体感**，属于纯负担。
- **MinIO**：解决的是**多机共享存储 / 多副本**。单机上它只是多一个要运维、要备份、要占资源的容器，
  与 §1.3 里的 K8s 同类。

---

## 2. 现状基线

> 本节描述**改造前的原始基线**（阶段 0 冻结的那一版），用于说明改造起点；
> 当前进度见 §4 与文末「迭代记录」。

| 维度 | 现状 |
| --- | --- |
| 代码 | Python 单体，CLI + Web 双入口，109 项测试全绿 |
| 数据 | **无数据库**；会话存 `data/conversations.json`（已 210 KB） |
| 任务 | **无任务概念**；接口同步阻塞，长任务容易超时 |
| 账号 | **无**；单用户，无隔离 |
| 凭据 | Cookie 明文存 `data/*-profile.json`；LLM Key 明文存 `config/zhiji.json` |
| 部署 | `start.bat` 本机启动；无容器化 |
| CI | GitHub Actions（ruff + mypy + pytest）已就位 |

### 2.1 可量化的痛点

1. `conversations.json` 已达 210 KB，**每次读写全量加载**，随使用线性恶化；
2. 凭据明文落盘，任何能读到文件的人都能拿走 Key 和 Cookie；
3. 生成笔记是**同步长请求**，页面一关任务就断（虽有 SSE 断连取消，但没有"后台继续跑"）；
4. 没有用户概念，无法区分"谁的笔记""谁的 Key"；
5. 没有历史与检索，产物散落在 `output/` 目录。

### 2.2 可复用的优质资产（改造中要保住）

- `platforms/` 适配器（抖音 / B站 / 知乎 / 小红书）——接口设计良好，**原样保留**；
- `pipeline.py` 编排逻辑——保留，外面套任务执行器；
- `llm/`、`transcription/`、`writers/`——保留，接入新存储层；
- 109 项测试——是改造的安全网，**不允许为了新功能而破坏**。

---

## 3. 改造原则

1. **不重写，只叠加**：先把新能力建在新路径上，最后再迁移老路径。
2. **测试即合同**：每一步结束必须 pytest / ruff / mypy 全绿，否则不进入下一步。
3. **小步可回滚**：每个迭代是一个独立提交，出问题能单独回退。
4. **不过早引入重型组件**：队列、消息中间件、K8s 一律推迟到真正需要时。
5. **向后兼容数据**：老格式（JSON / 明文配置）都要有一次性迁移脚本。
6. **每一步都能演示**：不接受“写了一堆代码但看不到效果”的迭代。
7. **表达与实现同步**：README、架构图、ADR 是交付物的一部分，随阶段随手维护，不把“让人看懂”攒到最后补。

---

## 4. 阶段总览

| 阶段 | 主题 | 关键产出 | 状态 |
| --- | --- | --- | --- |
| **0** | 基线冻结 | main 合并推送、CI、架构文档 | ✅ 已完成 |
| **1** | 工程底座与持久化 | SQLAlchemy + 数据库 + 任务表 | ✅ 已完成 |
| **2** | 异步任务闭环 | 任务状态机 + 后台执行 + 查询 API | ✅ 已完成 |
| **3** | 账号与数据隔离 | 用户表 + 登录 + 按用户隔离 | ✅ 已完成 |
| **4** | 凭据安全 | Key / Cookie 加密存储、健康检查与迁移 | ✅ 已完成（4.1–4.4） |
| **5** | 容器化部署 | Dockerfile + Compose + 部署文档 | ✅ 完成（5.1 镜像 / 5.2 Compose / 5.4 部署文档） |
| **5.5** | 产物落库（Obsidian 接入） | front-matter 稳定 ID + 幂等 upsert；输出目录可指向 vault | ✅ 完成 |
| **6** | Java 控制面（可选） | Spring Boot 承接鉴权 / 配额 / 编排 / 审计 | 🚧 进行中（6.1 完成） |
| **7** | 作品集交付 | README、图示、ADR 索引、演示与冒烟脚本 | 🚧 贯穿全程（收尾验收） |

**关于第 7 阶段**：它**不排在最后执行**——从当前阶段起就随每个迭代顺手维护（见 §3 原则 7 与阶段 7 说明），
否则到最后细节已经忘光。排在表格末尾只表示“最终验收放在收尾”。

**关于 5.5**：它排在阶段 5 之后、阶段 6 之前——不依赖 Java、不依赖容器化，却让“产物落进自己的知识库”
这条闭环成立。对所选受众（通用 / 独立开发）而言，它的收益高于阶段 6。

---

## 5. 阶段详细计划

### 阶段 1：工程底座与持久化

**目标**：引入数据库访问层，建立"任务"等核心表的持久化基础，并让默认零配置可跑。

| 迭代 | 内容 | 验收标准 |
| --- | --- | --- |
| ✅ **1.1 数据库底座** | 新增 SQLAlchemy 依赖；`storage/` 模块（engine / session / 表 / 仓储）；`ZHIJI_DATABASE_URL` 配置；任务表 CRUD；单元测试 | 默认生成 `data/zhiji.db`；任务表可增删查改；三件套全绿 |
| ✅ **1.2 会话落库** | `ChatStore` 从 JSON 改为数据库后端；保留一次性迁移脚本与回退开关 | 老会话可平滑迁移；`test_chat_store` 全绿 |
| ✅ **1.3 配置入库** | 模型配置（`config/zhiji.json`）迁入数据库；支持从文件导入 | 现有模型配置可导入；Web 配置页功能不回退 |

**新增 / 改动**

- `pyproject.toml`：+ `sqlalchemy`、+ `alembic`（迁移工具）
- 新增 `src/zhiji/storage/{__init__,db,tables,repository}.py`
- `src/zhiji/chat.py`：`ChatStore` 拆成「门面 + db / json 后端」，默认数据库后端，
  会话与消息分表存储（每次追加只 INSERT 一行，因此并发追加不丢消息）
- 新增 `src/zhiji/migrate.py`：`python -m zhiji.migrate [--dry-run]` 把旧 JSON 会话导入数据库（幂等）
- `.env.example`：+ `ZHIJI_DATABASE_URL`、+ `ZHIJI_CHAT_BACKEND`（`db` 默认 / `json` 回退）
- `src/zhiji/config.py`：`ConfigManager` 新增数据库后端（`model_configs` / `app_settings`），
  首次使用从 JSON 导入，写操作同时导出 JSON；`ZHIJI_CONFIG_BACKEND=json` 回退纯文件
- 新增 `src/zhiji/storage/config_repository.py`：配置 ORM 读写与整体替换语义
- `src/zhiji/migrate.py`：`python -m zhiji.migrate --config config/zhiji.json` 导入模型/设置
- 测试：`tests/test_storage.py`、`tests/test_chat_store.py`、`tests/test_migrate.py`、
  `tests/test_config_db.py`

**关键决策**：默认 **SQLite**（零运维、单机足够），通过 URL 切换 **PostgreSQL**（生产）。SQLAlchemy 让同一套代码适配两者。

**风险**：SQLite 并发写能力弱 → 单机单用户无影响；进入阶段 3（多用户）时评估切换到 PostgreSQL。

---

### 阶段 2：异步任务闭环

**目标**：把"同步长请求"改造成"提交任务 → 后台执行 → 查询结果"，页面关掉任务照样跑完。

| 迭代 | 内容 | 验收标准 |
| --- | --- | --- |
| ✅ **2.1 任务状态机** | `PENDING / RUNNING / SUCCEEDED / FAILED / CANCELLED`；状态流转校验；失败原因落库 | 状态流转有测试覆盖，非法流转被拒绝 |
| ✅ **2.2 后台执行器** | 进程内 worker（从任务表取活、执行、回写状态）；并发上限；优雅退出 | 提交任务后立刻返回 `taskId`；任务在后台跑完 |
| ✅ **2.3 任务 API** | `POST /api/tasks`、`GET /api/tasks/{id}`、`GET /api/tasks`、`POST /api/tasks/{id}/cancel` | 四个接口可用；SSE 可推送进度 |
| ✅ **2.4 笔记生成改造** | 现有"生成笔记"改走任务；前端改为"提交 → 轮询/订阅进度" | 关闭页面后任务仍完成；进度可见 |

**决策**：先用"数据库任务表 + 进程内 worker"，**不引入 Celery / RabbitMQ**。等出现多机需求再升级——届时接口不变，只换执行器。

---

### 阶段 3：账号与数据隔离

**目标**：从单用户变成多用户，数据严格隔离。

| 迭代 | 内容 | 验收标准 |
| --- | --- | --- |
| ✅ **3.1 用户表与密码** | `users` 表；bcrypt 哈希；注册 / 登录 / 登出 | 密码不可逆存储；接口有测试 |
| ✅ **3.2 会话与鉴权** | 签名会话 Cookie（或 JWT）；受保护路由统一鉴权 | 未登录访问受保护接口返回 401 |
| ✅ **3.3 数据隔离** | 业务表加 `user_id`；查询强制按用户过滤；默认用户迁移 | 两个用户互不可见（有测试证明） |
| ✅ **3.4 前端登录页** | 登录 / 注册界面；未登录切换登录页；退出入口 | 浏览器端四条路径已实际验证 |

**决策**：MVP 采用**自研轻量认证**（bcrypt + 签名会话），而不是立刻上 Casdoor。理由：单机 MVP 部署 Casdoor 的成本高于收益；设计上保留"认证提供方"抽象，未来对接 Casdoor/OIDC 时只需替换实现。（与 ARCHITECTURE 3.4.1 的差异在此说明：那是产品化阶段的选择，本计划按阶段推进。）

---

### 阶段 4：凭据安全

**目标**：LLM Key 与平台 Cookie 全部加密入库，明文文件清零。

| 迭代 | 内容 | 验收标准 |
| --- | --- | --- |
| ✅ **4.1 加密模块** | `cryptography` 实现 AES-256-GCM；主密钥来自环境变量 / 本机密钥文件 | 加解密与防篡改有测试；主密钥不入库、不入 Git |
| ✅ **4.2 LLM Key 入库** | `config/zhiji.json` 中的 Key 迁入 `llm_credential` 表（密文） | Key 明文只在写入瞬间出现；界面只显示掩码 |
| ✅ **4.3 Cookie 入库** | `data/*-profile.json` 迁入 `platform_credentials` 表（整份登录态密文） | 迁移后删除明文文件；抓取功能不回退 |
| ✅ **4.4 凭据健康检查** | 提供"检测凭据是否有效"接口；记录检测时间与结果 | 界面可手动触发检测并看到结果 |

**决策**：使用 `cryptography` 的 AES-GCM + 环境主密钥（MVP 够用）；上云后换 KMS/Vault，接口不变。

**4.1 落地细节**

- 新增 `src/zhiji/security/crypto.py`：`CredentialCipher`（AES-256-GCM，密文格式
  `v1.<nonce>.<ciphertext>`，随机 96 位 nonce，自带认证标签）；
- 主密钥解析顺序：显式参数 → `ZHIJI_MASTER_KEY` → `ZHIJI_MASTER_KEY_PATH` / `data/.master_key`
  → 自动生成并落盘（0600）；`create=False` 时缺失即报错，不会悄悄新建；
- 主密钥支持 URL-safe Base64 / 十六进制两种写法，长度不是 32 字节一律拒绝；
- 支持 AAD：把密文绑定到「用户 + 用途」，密文被复制到别的账号会解密失败；
- 解密失败（换密钥、篡改、AAD 不符、格式损坏）统一抛 `CryptoError`，绝不返回错误明文；
- `key_fingerprint()` 输出不可逆短指纹，便于日志与密钥轮换排查。

**4.1 验收**：`tests/test_crypto.py` 35 项用例覆盖加解密、随机性、篡改、错密钥、
AAD 绑定、格式损坏、密钥解析顺序与落盘；主密钥不入库、不进 Git。

**4.2 落地细节**

- 新增 `llm_credentials` 表（`user_id` / `name` / `key_cipher` / `key_hint` / `status` / 时间戳），
  `(user_id, name)` 唯一；新增 `src/zhiji/storage/credential_repository.py` 承载读写；
- 主密钥默认放在数据库同目录的 `.master_key`（`ZHIJI_MASTER_KEY` / `ZHIJI_MASTER_KEY_PATH` 仍可覆盖）；
  密文 AAD 绑定「用户 + 用途」，密文被搬到别的账号或别的模型名下会解密失败；
- `model_configs.api_key` 列与 `config/zhiji.json` 中的 Key 恒为空，密钥只以密文形式存在凭据表里；
  写配置时加密入库、读配置时解密回填、界面与日志只暴露 `前4****后4` 掩码；
- 一次性迁移 `python -m zhiji.migrate --migrate-keys`（幂等，支持 `--dry-run`）把历史明文 Key
  加密入库并清空明文列；迁移结束执行 `VACUUM` 重建库文件，抹掉 SQLite 空闲页里的旧明文残影。

**4.2 验收**：`tests/test_credentials.py`、`test_config_db.py`、`test_migrate.py` 覆盖往返加解密、
AAD 绑定、用户作用域隔离、更新不丢 Key、删除模型连带删凭据、迁移幂等与「库文件与旁文件均查不到明文」；
对真实 `data/zhiji.db` + `config/zhiji.json` 迁移后复查无任何明文残影，`ConfigManager` 仍能解密读到 3 个模型 Key。

**4.3 落地细节**

- 新增 `platform_credentials` 表（`user_id` / `platform` / `payload_cipher` / `hint` / `status` / 时间戳），
  `(user_id, platform)` 唯一：**整份登录态**（cookie / user_agent / saved_at / verified）序列化成 JSON 后
  一次性加密，库里没有 Cookie 的任何片段；
- 新增 `src/zhiji/storage/platform_credential_repository.py`；密文 AAD 为 `user:{作用域}/platform:{平台}`，
  密文被抄到别的平台或别的账号会解密失败；默认作用域是本机（`user_id IS NULL`），多用户时传 `user_id` 即可；
- 界面提示 `hint` 是零泄露的「6 项」（只报 Cookie 字段个数），不含任何 Cookie 值；
- `zhiji/platforms/cookies.py` 统一读写：默认走数据库；**显式传 `path` 时仍读写文件**（测试与临时场景），
  此时完全不碰数据库；
- 保存即入库并删除同目录遗留明文文件；读取时「库优先 → 回退读旧明文文件（读到就加密入库并删除文件）」，
  老数据不丢、明文清零；库里已有登录态时读取也会顺手清掉残留明文文件；
- 四个平台模块（抖音 / B 站 / 知乎 / 小红书）各加 `delete_profile()`，Web 的删除接口改为「删库行 + 删文件」，
  不再手动 `unlink`；
- 一次性迁移 `python -m zhiji.migrate --migrate-cookies`（幂等、支持 `--dry-run`；文件损坏时记 `invalid`
  并**保留原文件**便于人工检查），迁移后 `VACUUM` 重建库文件。

**4.3 验收**：`tests/test_platform_credentials.py`、`test_cookies.py`、`test_migrate.py`、`test_web.py`
覆盖往返加解密、零泄露提示、AAD 绑定平台、用户作用域隔离、空 Cookie 即删除、`configured()` 只查库不解密、
「库文件与旁文件均无明文 Cookie」、默认走库 / 显式 `path` 走文件、旧明文文件自动导入并删除、删除行为、
迁移幂等与 `--dry-run` 不写不删；本机 `data/` 已无明文 `*-profile.json`（4 个平台均无待迁移文件）。

**4.4 落地细节**

- 新增 `src/zhiji/health.py`：统一「一次探测」的模型（`CheckResult` / `ProbeOutcome`），状态取值与
  凭据表 `status` 列一致（`active` / `expired` / `error` / `unset`），另加只在界面出现的
  `unchecked`（已配置但没检测过）；
- **远程探测**：B 站 `x/web-interface/nav`（读 `data.isLogin`）与知乎 `/api/v4/me`（200 + `id`），
  带 Cookie 与 UA 发一次只读请求；HTTP 客户端以 `opener` 参数注入，测试传假实现即可，不必联网；
- **本地校验**：抖音 / 小红书的接口需要 `a_bogus` / `x-s` 签名，联网探测既不稳定也不礼貌，因此只检查
  关键会话 Cookie（`sessionid` / `web_session`）是否还在，并在 `source` 里如实标注，界面提示「未联网校验」；
- **零泄露**：失败摘要由本模块自己拼（HTTP 状态码、缺失的 Cookie 字段名、异常类名），或经
  `sanitize_reason` 抹掉 `Bearer …` / `sk-…` / `api_key=…` 之类片段后截断，任何情况下都不写入
  Cookie 或 Key 片段；
- 凭据表新增 `last_checked_at` / `last_error` 两列（启动时按需 `ALTER TABLE` 补列，兼容旧库）；
  两个仓储各加 `record_check(...)`（**只更新已存在的行，绝不新建**）与 `statuses(...)`（纯读库不发请求）；
- Web 端：`GET /api/credentials/health` 返回平台与模型的配置 / 健康总览，`POST /api/credentials/check`
  手动触发一次检测（body `{"kind": "platform"|"llm", "name": ...}`）并把结果写回；`/api/models` 与
  `/api/platforms` 的每个卡片也带上 `health` 字段；未配置的 Key 直接返回「未配置」而**不发请求**；
- 前端在「凭据」面板显示状态、检测时间与失败原因并提供「检测」按钮，模型卡片同理；
- 全程**不引入后台轮询 / 定时任务**，探测失败只落一行摘要，绝不影响抓取主流程。

**4.4 验收**：`tests/test_health.py`（新增 23 项）覆盖 B 站 / 知乎的 HTTP 错误、非 JSON、业务码、
`isLogin=false` 与风控错误码、抖音 / 小红书的本地结构校验、未知平台报错、探测异常兜底、摘要脱敏与截断；
`tests/test_credentials.py` / `tests/test_platform_credentials.py` 各新增 6 / 4 项，覆盖 `record_check`
只更新不新建、`active` 清空失败原因、超长摘要截断、作用域隔离；`tests/test_web.py` 新增 11 项，
覆盖总览结构、未配置 / 本地校验 / 探测异常 / 未配置 Key 四条检测路径与 `health` 字段下发。

---

### 阶段 5：容器化部署

**目标**：从"本机 start.bat"变成"一键部署"。

| 迭代 | 内容 | 验收标准 |
| --- | --- | --- |
| 🔶 **5.1 镜像构建** | 多阶段 Dockerfile；含 Playwright 依赖；非 root 运行 | 已实现：本机无 Docker，真实 `docker build` 交给 CI 的 docker job |
| **5.2 Compose 编排** | `docker-compose.yml`：应用 + 数据卷 + 健康检查；可选 PostgreSQL | `docker compose up` 后 Web 可访问 |
| **5.3 反向代理与配置**（附录） | Nginx/Caddy + HTTPS 说明；环境变量清单 | 仅当决定公网开放时才执行；自托管不需要 |
| **5.4 部署文档** | 安装、升级、备份、常见问题 | 新人可按文档独立部署 |

**5.1 落地细节**

- 新增根目录 `Dockerfile`（多阶段构建）：
  - **builder**：`python:3.12-slim-bookworm`，装 `build-essential` 只是兜底（默认依赖都有
    manylinux wheel），建 `/opt/venv`，用 **editable 安装**（`pip install -e .`）把包装到
    `/app/src`；这样 `PROJECT_ROOT` 仍是 `/app`，`data/` `config/` `output/` `.tmp/` 都落在
    卷挂载点上，而不是散进 `site-packages`；
  - **runtime**：同版本 slim 基础镜像，只拷 `/opt/venv` 与源码；装 `ffmpeg`（yt-dlp 抽音轨），
    并用 `playwright install --with-deps chromium` 把浏览器与系统库内置进镜像，浏览器固定在
    `/ms-playwright`，非 root 启动 Chromium 前给沙箱助手补 setuid 位；
  - **非 root 运行**：新增 `zhiji` 用户（uid/gid 10001），`USER zhiji` 之后不再有 root 步骤；
  - **状态外置**：`data/`（数据库 + `.master_key`）、`config/`、`output/`、`.tmp/` 声明为 `VOLUME`，
    镜像里只留空目录；`.dockerignore` 挡住 `.env`、`data/`、`config/`、`.master_key`、各类缓存
    与测试目录，密钥不会被 `docker build` 烤进镜像；
  - **自带健康检查**：`HEALTHCHECK` 用容器内 Python 请求 `http://127.0.0.1:$ZHIJI_PORT/api/health`
    （该接口在鉴权模式下公开），不依赖镜像里有没有 `curl`；
  - 可选 `--build-arg ZHIJI_WITH_ASR=1` 才装 `faster-whisper`，默认镜像不含语音转写依赖。
- 新增 `docker/entrypoint.sh`：默认按 `ZHIJI_HOST` / `ZHIJI_PORT`（0.0.0.0:8000）前台起 Web，
  并**默认追加 `--require-auth`**（容器绑的是 0.0.0.0，裸奔不可接受）；`ZHIJI_TOKEN` 可切回共享令牌，
  `ZHIJI_REQUIRE_AUTH=0` 才关闭（由 `zhiji web` 自身的安全检查兜底）；显式传命令时原样执行，
  例如 `docker run --rm -v zhiji-data:/app/data zhiji python -m zhiji.migrate --migrate-keys`；
  用 `exec` 启动，SIGTERM 直达 uvicorn，`docker stop` 能优雅退出。
- `.github/workflows/ci.yml` 新增 `docker` job：`docker build` → 检查容器内 `id -u` 是否为 10001
  （非 root）→ 起容器轮询 `/api/health` 直到 200 → 失败时打印容器日志。

**5.1 验收**：本机没有 Docker CLI（只剩 `com.docker.service` 残留），因此 `docker build` 未能在本机实跑，
真实构建 / 启动 / 健康检查放到 CI 的 `docker` job（push 后自动执行）。本机可验证的部分已全部通过：
`tests/test_packaging.py`（新增 11 项）守住入口脚本是 POSIX sh + LF、默认要求登录、显式命令透传、
入口脚本拼的 CLI 选项与 `zhiji web` 真实选项一致、Dockerfile 非 root + Playwright + 健康检查 + 卷挂载 +
不烤密钥、`.dockerignore` 挡住本机状态与密钥；`pytest` / `ruff` / `mypy` / `node --check` 全绿。
手动验证命令：构建 `docker build -t zhiji:5.1 .`，
运行 `docker run --rm -p 8000:8000 -v zhiji-data:/app/data -v zhiji-config:/app/config zhiji:5.1`。

**5.2 落地细节**

- `docker-compose.yml`：单服务 `zhiji`（构建本仓库镜像）+ 四个具名卷（`data` /
  `config` / `output` / `.tmp`）+ 显式健康检查（打 `/api/health`）；默认端口
  只绑 `127.0.0.1:8000`，`ZHIJI_BIND=0.0.0.0` 才对局域网开放。
- 环境变量集中在 `.env`（由 `.env.example` 复制），新增 `ZHIJI_BIND` /
  `ZHIJI_HOST_PORT` / `POSTGRES_*`；未提供一律走 compose 默认值。
- 可选 PostgreSQL 档：放在 `profiles: ["postgres"]` 默认不启动；需先
  `docker compose build --build-arg ZHIJI_WITH_POSTGRES=1` 把 `psycopg` 装进镜像，
  再 `--profile postgres up -d` 并把 `ZHIJI_DATABASE_URL` 指向 postgres 服务。
  表结构由 `Database.create_all()` 在首次启动时自动建，`python -m zhiji.migrate`
  只用于导入旧版 JSON 数据。
- vault 接入：在 `volumes` 加一行 bind mount（如 `- D:/Vault/Zhiji:/vault`），
  再到 Web「设置 → 保存路径」填 `/vault`（配置入库，持久在 `zhiji-config` 卷）。

**5.2 验收**：本机无 Docker CLI，真实 `docker compose up` 交给 CI。新增
`tests/test_compose.py` 守住服务名 / 镜像 / 默认回环绑定 / 四个具名卷 / 健康检查 /
postgres 默认不启动 / 迁移命令必须是 `python -m zhiji.migrate`（跨文件契约）；
CI 的 `docker` job 增加 `docker compose -f docker-compose.yml config -q` 做真实校验。

**5.4 落地细节**

- 新增 `docs/DEPLOY.md`：安装（clone + `.env` + `docker compose up -d` + 首次注册）、
  环境变量表、数据与卷的备份 / 恢复、升级流程、可选 PostgreSQL、Obsidian vault
  接入（bind mount + 设置页保存路径）、常见问题 FAQ。
- `README.md` 增加「部署（Docker Compose）」一节并指向 `docs/DEPLOY.md`。

**5.4 验收**：新人按 `docs/DEPLOY.md` 可独立完成单机部署；`pytest` / `ruff` /
`mypy` 全绿。

---

### 阶段 5.5：产物落库与 Obsidian 接入

**目标**：让产物从“趴在 `output/` 目录里”变成“落进用户自己的知识库”，并且**重复处理同一素材不会产生重复笔记**。

**为什么排在这里**：这一步不依赖 Java、不依赖队列、不依赖任何额外服务，是当前投入产出比最高的“闭环补完”。
它同时服务两个目标——对使用者，笔记直接进 vault；对评审者，“产物 → 知识库”这条链路终于连成一条线。

| 迭代 | 内容 | 验收标准 |
| --- | --- | --- |
| **5.5.1 稳定 ID** | front-matter 增加 `zhiji_id`，沿用既有的 `artifacts/<platform>_<content_id>` 标识；渲染与写入都带上 | 同一素材两次产出的 `zhiji_id` 相同 |
| **5.5.2 幂等 upsert** | 按 `zhiji_id` 查找：命中则**原地更新**，未命中才新建；替换 `_unique_path()` 的“重复即加序号”行为 | 同一素材重跑不产生 `(1).md`；用户手写批注的取舍有明确规则并写进文档 |
| **5.5.3 输出目录指向 vault** | `output_dir` 可指向 vault 子目录（如 `D:\Vault\Zhiji`）；文档写清 front-matter 与 vault 的对应关系 | 指向 vault 后 Obsidian 直接索引，无需插件 |
| **5.5.4（可选）REST 后端** | 为将来的跨机部署补一个 `obsidian-local-rest-api` 写出后端，接口与文件后端一致 | 两种后端可切换；测试用假实现，不联网 |

**决策（v1.2 新增，见 §8 D12）**：接入方式取**直接写 vault 目录**，而不是 `obsidian-local-rest-api`。
理由：自托管单机形态下应用与 vault 同机，直连零依赖、免装插件；跨机部署时再补 5.5.4。

**风险**：整篇覆盖可能吃掉用户在笔记里手写的批注 → 5.5.2 必须明确取舍（建议：只覆盖知记生成的部分，
或把生成内容放在固定的区段标记之间，区段之外一律不动）。

**5.5 落地细节**

- **5.5.1 稳定 ID**：`NoteFrontmatter` 新增必填 `zhiji_id`，值取既有的
  `{platform}_{content_id}`；`pipeline._build_note` 与 `render_note` 都带上，
  front-matter 里排在 `title` 之前。
- **5.5.2 幂等 upsert**：`MarkdownWriter.write` 先按 `zhiji_id` 在输出目录扫
  已有 `*.md`（廉价字符串预筛 + 解析 front-matter），命中→**原路径原地更新**，
  未命中→走原 `_unique_path` 新建。取舍规则采用**区段标记**：生成内容包在
  `<!-- zhiji:begin -->` 与 `<!-- zhiji:end -->` 之间，更新时只覆写这段；
  **区段之后**用户手写的批注保留。旧文件没有标记则整篇覆写，并在
  `OutputReceipt.warnings` 里告警；新增 `OutputReceipt.updated` 标识新建 / 更新。
- **5.5.3 指向 vault**：代码侧 `resolve_output_dir()` 已支持绝对路径，无需改动；
  容器下须 **bind mount**（如 `- D:/Vault/Zhiji:/vault`）再在 Web「设置 → 保存路径」
  填 `/vault`（配置入库，持久在 `zhiji-config` 卷）。详见 `docs/DEPLOY.md`。
- **5.5.4 REST 后端**：跳过（跨机部署时再补，D12）。

**5.5 验收**：`tests/test_pipeline.py` 重写原 `test_markdown_writer_avoids_overwrite`
为三条：同 `zhiji_id` 三次写入落同一路径且无 `-2.md`、区段后手写批注保留、不同 id
互不覆盖；380 passed / 1 skipped / ruff 全绿 / mypy 47 files。

---

### 阶段 6：Java 控制面（可选，最后做）

**目标**：把“传统后端技术栈”落地——Java 接管控制面，Python 退为数据面 worker。

**切分原则（v1.1 修订）**：**按责任切，不按接口切。**

把 Python 已有的三组接口（账号 / 任务 / 凭据）用 Spring Boot 重写一遍，在评审眼里是
“同一套 CRUD 写了两遍”，**是需要避免的负信号**。Java 应当承担传统后端真正难的那部分。

| 迭代 | 内容 | 验收标准 |
| --- | --- | --- |
| **6.1 契约固化** | OpenAPI 定义控制面 ↔ worker 接口；Python 侧实现 + 双向契约测试；调用链 id 透传 | 契约测试通过；改契约两边同时红 |
| **6.2 鉴权与身份** | Spring Security 做 OAuth2 / JWT 资源服务器，替掉自研 bcrypt + HMAC；RBAC + 多租户 claim | 标准协议可被第三方工具校验；令牌可刷新、可吊销 |
| **6.3 配额与限流** | Redis 令牌桶（按平台独立 QPS）+ Resilience4j 熔断降级 | 压测下平台维度限流生效；平台不可用时可降级 |
| **6.4 编排与可观测** | RabbitMQ：重试 / 死信 / 幂等键 / 补偿；Micrometer + Prometheus，trace 跨语言传播 | 跨进程链路可追踪；重复投递不产生重复任务 |
| **6.5 审计** | 只增不改的审计表 + outbox 事件（接上 4.4 的凭据健康检查） | 凭据使用与任务流转可回溯到人 |

**边界**：Python 侧**只保留数据面**——平台适配器、浏览器、转写、LLM 加工。这条边界由**能力**划出，
而不是由接口划出，这才是本阶段的架构价值所在。

**说明**：本阶段建立在阶段 1–5 的成果之上，不是另起炉灶。届时数据库、凭据、任务模型都已稳定，
Java 只是换一个实现。

---

### 阶段 7：作品集交付（贯穿执行，收尾验收）

**目标**：让“工程做得好”这件事**被看见**。阶段 1–6 的验收标准全是测试三件套，它们证明代码是对的，
却不证明**别人看得懂**。本阶段补的就是这一公里。

| 迭代 | 内容 | 验收标准 |
| --- | --- | --- |
| **7.1 README 重构** | 30 秒说清“是什么 / 给谁用 / 一条命令怎么跑”，链接架构图与 ADR | 陌生人不用问人就能启动 |
| **7.2 图示** | 架构图、数据流图、任务状态机图（Mermaid 内嵌，与代码一起版本化） | 图与代码一致，可直接渲染 |
| **7.3 ADR 索引与补录** | 把 §8 扩成独立 `docs/adr/`，补记每条决策的背景 / 备选 / 推翻条件 | 每条决策都能回答“为什么不用另一种” |
| **7.4 演示与冒烟脚本** | 3 分钟录屏 + 一键启动脚本 + 端到端冒烟脚本 | 从零到跑通 ≤ 10 分钟 |

**执行方式**：7.1–7.3 是**持续动作**，每完成一个阶段顺手更新（§3 原则 7），不要攒到最后——
那时细节与判断都已经忘了。7.4 在阶段 6 收尾后做。

**验收**：一个没读过本仓库的人，只凭 README + 一条命令，10 分钟内跑通一次笔记生成。

---

## 6. 执行方式（工作流约定）

每个迭代按固定节奏推进：

1. **实现**：只做当前迭代范围内的事，不顺手夹带；
2. **验证**：跑 `pytest` / `ruff check` / `mypy` 三件套，必须全绿；
3. **记录**：写清楚"改了什么、为什么、怎么验证的"；
4. **汇报**：报告结果并提出下一步，**确认后再继续**。

**约定**：任何一步如果导致原有测试失败，优先修复而不是删测试；确需修改既有行为时，必须说明原因。

---

## 7. 风险与应对

| # | 风险 | 影响 | 应对 |
| --- | --- | --- | --- |
| 1 | 范围失控，想做的东西太多 | 高 | 严格按 §1.3 非目标清单执行，超出范围的一律记入"待办" |
| 2 | 改造中破坏现有功能 | 高 | 每步三件套全绿；新增路径优先，老路径最后迁移 |
| 3 | SQLite 并发瓶颈 | 中 | 阶段 3 评估切 PostgreSQL，代码无需改动 |
| 4 | 凭据迁移过程中泄露 | 高 | 迁移脚本只在本地运行；迁移完成后清理明文文件 |
| 5 | 网络/代理导致依赖安装失败 | 中 | pip 与 git 均走本地代理 `127.0.0.1:7897`（见部署说明） |
| 6 | 一次性投入过大导致停滞 | 高 | 每阶段都有可演示成果，随时可以停在一个可用状态 |
| 7 | 作品集最常见的死法：广度做大、没有一样做透 | 高 | 严守 §1.3；平台数量不扩张；先做透闭环与文档 |
| 8 | 工程做完了但没人看得懂（README / 图示 / ADR 缺失） | 高 | 阶段 7 贯穿执行，每个阶段顺手更新 |

---

## 8. 关键决策记录（ADR 摘要）

| 编号 | 决策 | 理由 | 何时可推翻 |
| --- | --- | --- | --- |
| D1 | 数据库用 SQLAlchemy，默认 SQLite | 零运维起步，且能平滑切 PostgreSQL | 多用户并发写入出现瓶颈时 |
| D2 | 异步先用"任务表 + 进程内 worker" | 避免过早引入 MQ，单机足够 | 需要多机部署时换 Celery/RQ |
| D3 | 认证先自研轻量（bcrypt + 签名会话） | MVP 阶段部署 Casdoor 成本高于收益 | 需要对外开放注册时 |
| D4 | 语言策略：先 Python，后 Java 控制面 | 避免"边学 Java 边改业务"的双重风险 | 阶段 6 |
| D5 | 凭据加密用 cryptography + 环境主密钥 | 简单可靠，无外部依赖 | 上云后用 KMS/Vault |
| D6 | 不重写 `platforms/` 适配器 | 现有实现质量良好，是核心资产 | — |
| D7 | 会话默认入库，保留 `ZHIJI_CHAT_BACKEND=json` 回退 | 消息表天然支持并发追加；回退开关让迁移可逆 | 并发写入成为瓶颈时切 PostgreSQL |
| D8 | 产品形态：自托管优先，不做对外开放注册的 SaaS | 公网开放把项目性质从“工程质量”变成“运维与合规责任”，对本项目两个目标都几乎无增益 | 明确要做开放注册的产品，且愿意承担备案 / 滥用 / 成本时 |
| D9 | 阶段 6 按**责任**切分，不按接口重写 | 重写同类 CRUD 是负信号；Java 的价值在鉴权协议、分布式限流、编排补偿、可观测 | 若 Java 侧只做只读聚合之类的薄层 |
| D10 | 作品集交付（阶段 7）贯穿执行，不攒到最后 | 细节与判断会随时间遗忘，而 README / ADR 恰是评审最先看到的部分 | — |
| D11 | 受众优先级取**通用 / 独立开发** | 作品的读者是评审而非终端用户；自托管 + 完整闭环最能体现工程深度，Java 提前上马反而拖慢闭环 | 转向 Java 后端岗或 AI / Agent 岗作品集时 |
| D12 | Obsidian 接入取**直接写 vault 目录**，而非 Local REST API 插件 | 自托管单机形态下应用与 vault 同机，直连零依赖、免装插件；`ARCHITECTURE.md` §3.1 推荐的 REST 方案只在跨机时才必要 | 进入多机 / 云部署时（届时补 5.5.4 的 REST 后端） |

---

## 9. 下一步

**阶段 5 已关闭（5.2 Compose / 5.4 部署文档 / 5.5 Obsidian 接入全部完成）**

下面是 5.2 当时的计划与验收，保留作为记录：

5.1 已经把「本机 start.bat」换成可构建的镜像：

- 根目录 `Dockerfile`（多阶段 + 非 root uid 10001 + 内置 Playwright/Chromium 与 ffmpeg），
  以 editable 方式装到 `/app/src`，保证 `data/` `config/` `output/` `.tmp/` 都落在卷挂载点；
- `docker/entrypoint.sh` 默认 0.0.0.0:8000 起 Web 且默认要求登录，显式传命令时透传
  （迁移、体检等一次性任务直接 `docker run ... python -m zhiji ...`）；
- `.dockerignore` 挡住 `.env` / `data/` / `config/` / `.master_key`，密钥不进镜像层；
- CI 增加 `docker` job 做真实构建 + 启动 + `/api/health` 探测 + 非 root 断言。

5.2 要做的具体内容：

1. `docker-compose.yml`：应用服务 + 具名数据卷（数据库、配置、输出）+ 健康检查依赖；
2. 环境变量清单（主密钥、鉴权开关、数据库连接串、端口）集中到 `.env` 示例并在文档里说明；
3. 可选 PostgreSQL 档：改 `ZHIJI_DATABASE_URL` 即可切换，代码无需改动；
4. `docker compose up` 后 Web 可访问、数据在卷里持久化，重启不丢。

**验收**：`docker compose config` 无告警；`docker compose up -d` 后 `/api/health` 返回 200；
容器重启后数据仍在；`pytest` / `ruff` / `mypy` / `node --check` 全绿。

**修订后的顺序（v1.1）**：先做 **5.2 Compose + 5.4 部署文档**，让“一条命令起在自己机器上”成立；
5.3（反代 / HTTPS）降为**附录**，只在决定公网开放时才动。

**下一步**：阶段 5 已全部完成（5.2 / 5.4 / 5.5），本机提交未推送。后续可按需启动
阶段 6（Java 控制面，可选）或推进阶段 7（作品集交付）。公网部署需要时再补 5.3 反代 / HTTPS 附录。

### 迭代记录

| 迭代 | 状态 | 验证 |
| --- | --- | --- |
| 1.1 数据库底座 | ✅ 完成 | 120 passed / ruff 全绿 / mypy 36 files |
| 1.2 会话落库 | ✅ 完成 | 138 passed / ruff 全绿 / mypy 37 files；22 个真实会话、54 条消息迁移后与 JSON 一致 |
| 1.3 配置入库 | ✅ 完成 | 153 passed / ruff 全绿 / mypy 38 files；真实 3 个模型与设置已导入 `data/zhiji.db`，JSON 保留 |
| 2.1 任务状态机 | ✅ 完成 | 170 passed / ruff 全绿 / mypy 38 files；合法流转放行、非法流转与失败缺原因被拒绝 |
| 2.2 后台执行器 | ✅ 完成 | 185 passed / ruff 全绿 / mypy 39 files；数据库即队列、优先级取活、协作式取消、提交即返回并在后台跑完 |
| 2.3 任务 API | ✅ 完成 | 191 passed / ruff 全绿 / mypy 39 files；提交/查询/列表/取消/SSE 五接口，Web 生命周期自动启停 worker |
| 2.4 笔记生成改造 | ✅ 完成 | 195 passed / ruff 全绿 / mypy 39 files；生成接口提交即返回任务 id，断连/关页后台仍跑完，SSE 可订阅 stage/delta/progress 并回放历史，前端按 task id 刷新恢复 |
| 3.1 用户表与密码 | ✅ 完成 | 214 passed / ruff 全绿 / mypy 41 files；bcrypt 哈希落库、注册/登录/登出/me 四接口、HMAC 会话令牌可跨进程重启 |
| 3.2 会话与鉴权 | ✅ 完成 | 224 passed / ruff 全绿 / mypy 41 files；统一鉴权中间件、`--require-auth` / `ZHIJI_REQUIRE_AUTH` 开关、Cookie 与 Bearer 双通路、旧 `--token` 兼容 |
| 3.3 数据隔离 | ✅ 完成 | 232 passed / ruff 全绿 / mypy 42 files；会话 / 任务写入绑定用户，跨用户读写删返回 404 / 空，`NULL` 历史数据本机作用域与旧库补列迁移均有测试覆盖 |
| 3.4 前端登录页 | ✅ 完成 | 232 passed / ruff 全绿 / mypy 42 files / `node --check` 通过；临时数据库 + Edge 无头浏览器实际验证注册、登录、刷新保持会话、登出，匿名时业务页保持隐藏 |
| 4.1 加密模块 | ✅ 完成 | 267 passed / ruff 全绿 / mypy 44 files；加解密、随机 nonce、篡改、错密钥、AAD 绑定、密钥解析顺序与落盘全覆盖；主密钥不入库、不进 Git |
| 4.2 LLM Key 入库 | ✅ 完成 | 285 passed / ruff 全绿 / mypy 45 files / `node --check` 通过；Key 明文不再落库、JSON 导出与界面只给掩码、迁移幂等且 `VACUUM` 后库文件无明文残影；真实库迁移后仍可解密读回 3 个 Key |
| 4.3 Cookie 入库 | ✅ 完成 | 311 passed（新增 26 项）/ ruff 全绿 / mypy 46 files / `node --check` 通过；登录态整份密文入库、界面只给「N 项」零泄露提示、AAD 绑定平台与作用域、默认走库而显式 `path` 仍走文件、旧明文文件自动导入并删除、迁移幂等且可 `--dry-run`；本机 `data/` 已无明文 `*-profile.json`（无待迁移文件），真实库 `platform_credentials` 为空表，库文件与旁文件均无明文 Cookie 残影 |
| 4.4 凭据健康检查 | ✅ 完成 | 355 passed（新增 44 项）/ ruff 全绿 / mypy 47 files / `node --check` 通过；B 站 / 知乎带 Cookie 发一次只读身份请求、抖音 / 小红书只做本地关键 Cookie 校验并标注「未联网校验」，结果写回凭据行且失败摘要脱敏截断（绝不含 Cookie / Key 片段），未配置的 Key 不发请求，无任何后台轮询；`/api/credentials/health` 只读总览、`/api/credentials/check` 手动触发并写回、`/api/models` 与 `/api/platforms` 下发 `health` 字段 |
| 5.1 镜像构建 | 🔶 已实现，待真实构建验证 | 新增 `tests/test_packaging.py` 11 项守住入口脚本与 Dockerfile 不变量 / ruff 全绿 / mypy 47 files / `node --check` 通过；本机无 Docker CLI，`docker build` + 容器启动 + `/api/health` 由新增的 CI docker job 覆盖 |
| 计划书 v1.1 修订 | ✅ 完成 | 定位收敛为自托管形态；新增第 7 阶段（作品集交付）；阶段 6 改为按责任切分；补 D8–D10；新增 §1.4 目标与受众；修正阶段总览过期状态 |
| 受众优先级落定 | ✅ 完成 | 定为「通用 / 独立开发」：自托管 + 完整闭环优先，阶段 6 保持可选、最后做；§0 新增「受众」行，§1.4 / §9 同步更新，ADR 补 D11 |
| 计划书 v1.2 修订 | ✅ 完成 | 新增 §1.5 扩展候选（区分「没排期」与「不做」）；Obsidian 幂等落盘由“远期”升格为**阶段 5.5**（含 4 个子迭代与验收标准）；§1.3 补记单机对象存储为非目标；ADR 补 D12 |
| 5.2 Compose 编排 | ✅ 完成 | 新增 `docker-compose.yml`（单服务 + 四具名卷 + 健康检查 + 可选 postgres 档）/ `.env.example` 补 `ZHIJI_BIND` 等变量 / `Dockerfile` 加 `--build-arg ZHIJI_WITH_POSTGRES` / `pyproject` 加 `postgres` 可选依赖 / `tests/test_compose.py` 守不变量 / CI 加 `docker compose config -q`；`python -m zhiji migrate` 笔误一并修正 |
| 5.4 部署文档 | ✅ 完成 | 新增 `docs/DEPLOY.md`（安装/升级/备份/PostgreSQL/Obsidian vault/FAQ）/ `README.md` 增部署一节 |
| 5.5 Obsidian 接入 | ✅ 完成 | `NoteFrontmatter` 加必填 `zhiji_id` / `MarkdownWriter` 按 id 幂等 upsert + 区段标记（`begin/end`）保留区段外手写批注 / `OutputReceipt.updated` / 重写 `test_markdown_writer_avoids_overwrite` 为三条用例；380 passed / 1 skipped / ruff 全绿 / mypy 47 files |
| 6.1 契约固化 | ✅ 完成 | OpenAPI 契约 docs/contracts/worker-api.yaml（dispatch/cancel/health + callback events，schema 1.0）/ Python worker 模块 src/zhiji/worker/（models/trace/callback/service/api）+ 19 项契约测试 / Java Spring Boot 控制面 control-plane/（model/service/client/controller）+ 19 项契约测试；trace id 跨语言透传；398 passed / 1 skipped / ruff 全绿 / mypy 53 files / mvn test 全绿 / 
ode --check 通过 |
