# 知记（zhiji）部署指南

本文档面向**自托管单机部署**：一台装了 Docker 的机器上 `docker compose up -d`
就能跑起来，数据落具名卷、重启不丢。公网开放是后续可选步骤，见末尾「公网部署」。

> v0.1.0 的交付形态是**桌面浏览器工作台 + 自托管单机**。手机端不属于本版本验收范围；SaaS 多租户、计费与运营后台也未纳入本版本。

> 没有 Docker 也能用：见本文档末尾「无 Docker：本机直接运行」。

---

## 1. 前置条件

- 一台 Linux / macOS / Windows 机器，已安装 **Docker Engine + Docker Compose v2**
 （`docker compose version` 能输出版本号即可）。
- 一个兼容 OpenAI 接口的 LLM 服务（DeepSeek、阿里百炼等），拿到 API Key。
- （可选）Obsidian vault 所在目录，用于让笔记直接写进知识库（见 §5）。

> Windows 下 Docker Desktop 的卷挂载路径要用正斜杠（如 `D:/Vault/Zhiji`），
> 见 §5。

---

## 2. 快速开始

```bash
git clone <仓库地址> zhiji && cd zhiji
cp .env.example .env          # Windows: copy .env.example .env
# 按需编辑 .env（不编辑也能用默认值）
docker compose up -d           # 构建并后台启动（构建阶段自动打包 Vue 工作台）
docker compose ps              # 等到 STATE 显示 healthy
```

镜像构建会自动执行前端 `npm ci` + `npm run build`，运行时直接托管 `frontend/dist/`。浏览器打开 **http://127.0.0.1:8000**：

1. 首次访问会进入注册页——**注册一个账号**（第一个账号即管理员，之后可在设置里关闭注册）。
2. 「设置 → 模型」添加一个 LLM 模型（名称 / API Key / Base URL / 模型 ID）。
3. 「设置 → Cookie」按页面提示粘贴各平台 Cookie（B 站需含 `SESSDATA`，知乎需含
   `d_c0`，抖音需含 `sessionid`，小红书需含 `web_session`）。
4. 回到首页选择平台、粘贴链接，生成笔记。

> 默认只监听 `127.0.0.1`，**只有本机能访问**。要让局域网其他设备访问见 §4。

常用命令：

| 命令 | 作用 |
| --- | --- |
| `docker compose up -d` | 后台启动 |
| `docker compose logs -f zhiji` | 跟随日志 |
| `docker compose ps` | 查看状态（含健康检查） |
| `docker compose restart zhiji` | 重启应用 |
| `docker compose down` | 停止并移除容器（**具名卷保留**） |
| `docker compose down -v` | 连数据卷一起删除（**清空所有数据**） |

---

## 3. 环境变量

复制 `.env.example` 为 `.env` 后按需修改。未填的项一律走 `docker-compose.yml`
里的默认值。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ZHIJI_BIND` | `127.0.0.1` | 对外监听地址；`0.0.0.0` 才允许局域网访问 |
| `ZHIJI_HOST_PORT` | `8000` | 宿主机端口（容器内固定 8000） |
| `ZHIJI_REQUIRE_AUTH` | `1` | 1=要求账号登录；0=关闭（容器绑 0.0.0.0 时强烈不建议关闭） |
| `ZHIJI_TOKEN` | （空） | 旧版共享访问令牌；设了可用 `?token=` 访问，兼容老用法 |
| `ZHIJI_MASTER_KEY` | （空） | 凭据加密主密钥；留空则自动生成并落盘到 `data/.master_key` |
| `ZHIJI_SECRET_KEY` | （空） | 会话签名密钥；留空则自动生成 `data/.session_secret` |
| `ZHIJI_DATABASE_URL` | （空） | 留空用 SQLite；指向 postgres 见 §6 |
| `ZHIJI_SESSION_TTL` | `604800` | 会话有效期（秒），默认 7 天 |
| `ZHIJI_BCRYPT_ROUNDS` | `12` | bcrypt 代价因子 |

> **主密钥务必备份**：`ZHIJI_MASTER_KEY` 留空时自动生成的 `data/.master_key`
> 一旦丢失，已加密的 LLM Key / Cookie 无法恢复，只能重新录入。

---

## 4. 让局域网 / 其他设备访问

在 `.env` 里：

```env
ZHIJI_BIND=0.0.0.0
ZHIJI_HOST_PORT=8000
```

然后 `docker compose up -d`。其他设备用 `http://<本机IP>:8000` 访问。

> ⚠️ 绑 `0.0.0.0` 会暴露 API Key / Cookie 管理接口。**不要直接对公网开放**；
> 公网请走反向代理 + HTTPS（见末尾「公网部署」），并保持 `ZHIJI_REQUIRE_AUTH=1`。

---

## 5. 接入 Obsidian vault

知记的产物本身就是带 YAML front-matter 的 Markdown。把输出目录指向你的 vault
子目录，Obsidian 就能直接索引，**无需装任何插件**。

### 5.1 挂载 vault 目录

在 `docker-compose.yml` 的 `zhiji` 服务 `volumes` 里加一行 bind mount（取消注释
或新增）：

```yaml
    volumes:
      - zhiji-data:/app/data
      - zhiji-config:/app/config
      - zhiji-output:/app/output
      - zhiji-tmp:/app/.tmp
      # 把你的 vault 目录挂进容器的 /vault
      - D:/Vault/Zhiji:/vault
```

> Windows 路径用正斜杠（`D:/Vault/Zhiji`）；Linux / macOS 用 `/home/you/Vault/Zhiji`。
> 容器内 uid 是 10001，确保宿主机目录对该用户可写（Linux 下 `chown -R 10001 vault/`）。

### 5.2 设置保存路径

启动后进 Web「设置 → 保存路径」，填 **`/vault`**（或 vault 下的子目录，如
`/vault/知记`）。配置会入库，持久化在 `zhiji-config` 卷，重启不丢。

> `OUTPUT_DIR` 环境变量目前不被代码读取，**保存路径以设置页为准**。

### 5.3 幂等更新与手写批注

同一素材重复处理时，知记按 front-matter 里的 `zhiji_id` 找到原文件**原地更新**，
不会产生 `-2.md` / `(1).md`。生成内容包在区段标记之间：

```
<!-- zhiji:begin -->
…知记生成的正文…
<!-- zhiji:end -->
```

- 标记**之间**的内容每次会被覆写；
- 标记**之后**你手写的内容会保留；
- 旧文件（没有标记）会整篇覆写，并在生成结果里告警。

> front-matter（`zhiji_id` / `title` / `tags` 等）由知记维护，每次更新会刷新。
> 想加自定义标签，请在 `<!-- zhiji:end -->` 之后的正文里写。

---

## 6. 可选：切换到 PostgreSQL

默认用 SQLite（落在 `zhiji-data` 卷里的 `data/zhiji.db`），单机完全够用。要用
PostgreSQL：

1. 构建带驱动的镜像（`psycopg` 默认不进镜像）：
   ```bash
   docker compose build --build-arg ZHIJI_WITH_POSTGRES=1
   ```
2. 在 `.env` 里：
   ```env
   ZHIJI_DATABASE_URL=postgresql+psycopg://zhiji:zhiji@postgres:5432/zhiji
   ```
3. 启动时带上 postgres profile：
   ```bash
   docker compose --profile postgres up -d
   ```

表结构在应用首次启动时由 `Database.create_all()` 自动创建。`python -m zhiji.migrate`
只用于把旧版 JSON 会话数据导入，新部署不需要：

```bash
docker compose run --rm zhiji python -m zhiji.migrate
```

---

## 7. 备份与恢复

所有状态在四个具名卷里：`zhiji-data`（数据库 + 主密钥）、`zhiji-config`、
`zhiji-output`（笔记）、`zhiji-tmp`。

### 备份

```bash
# 备份整个 data 卷（含数据库 + 主密钥 + 会话密钥）
docker run --rm -v zhiji_zhiji-data:/data -v "$PWD/backup":/backup alpine \
  tar czf /backup/zhiji-data-$(date +%F).tar.gz -C /data .

# 配置卷同样备份（可选，配置已在数据库里）
docker run --rm -v zhiji_zhiji-config:/data -v "$PWD/backup":/backup alpine \
  tar czf /backup/zhiji-config-$(date +%F).tar.gz -C /data .
```

> 卷名前缀是 `<compose 项目名>_`，本项目名固定为 `zhiji`，所以全名是
> `zhiji_zhiji-data`。用 `docker volume ls` 确认。

### 恢复

```bash
docker compose down
docker run --rm -v zhiji_zhiji-data:/data -v "$PWD/backup":/backup alpine \
  sh -c "rm -rf /data/* && tar xzf /backup/zhiji-data-YYYY-MM-DD.tar.gz -C /data"
docker compose up -d
```

> **数据库与主密钥必须一起备份 / 一起恢复**：只有库没有 `data/.master_key`，
> 里面的加密凭据无法解密。

---

## 8. 升级

```bash
git pull
docker compose build          # 重新构建镜像
docker compose up -d          # 用新镜像重启（数据在卷里不丢）
# 如版本间有数据迁移：
docker compose run --rm zhiji python -m zhiji.migrate
```

SQLite 升级前建议先按 §7 备份 `zhiji-data` 卷。

---

## 9. 常见问题

**Q：首次访问为什么是注册页？**
A：容器默认 `ZHIJI_REQUIRE_AUTH=1`，第一个注册的账号即为使用者。注册后即可登录使用。

**Q：端口 8000 被占用？**
A：在 `.env` 里改 `ZHIJI_HOST_PORT=8001`（或其他空闲端口），再 `docker compose up -d`。

**Q：健康检查一直不健康？**
A：`docker compose logs zhiji` 看启动日志。常见是 `.env` 里 `ZHIJI_DATABASE_URL`
指向了连不上的库，或容器内 uid 10001 对挂载目录无写权限（Linux 下 `chown -R 10001`）。

**Q：忘记密码 / 想删账号？**
A：目前没有自助找回。可进入数据库直接操作 `users` 表（SQLite：
`docker compose exec zhiji python -c "import sqlite3;..."`），或最暴力的
`docker compose down -v` 重来（会清空所有数据）。

**Q：Windows 下 bind mount 报错？**
A：Docker Desktop 需在设置里共享对应盘符；路径用正斜杠 `D:/Vault/Zhiji`。

**Q：本机没装 Docker，能不能跑？**
A：能，见下面「无 Docker」。

---

## 10. 无 Docker：本机直接运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,asr]"
playwright install chromium
zhiji check-env
cd frontend
npm ci
npm run build      # 必须先构建，FastAPI 才能托管 Vue 工作台
cd ..
zhiji web          # 默认 http://127.0.0.1:8000
```

Windows 上也可直接运行 `scripts\quickstart.bat`，脚本会完成上述前端构建与数据库迁移。

详见 `README.md` 与 `docs/RELEASE_ACCEPTANCE.md`。Obsidian vault 接入同样只需在「设置 → 保存路径」填 vault 的
**绝对路径**（如 `D:\Vault\Zhiji`），无需挂载。

---

## 11. 公网部署（可选，后续）

当前形态是**自托管单机**，默认只听 `127.0.0.1`。若要公网开放：

1. 用反向代理（Nginx / Caddy）终结 HTTPS，回源到 `127.0.0.1:8000`；
2. 保持 `ZHIJI_REQUIRE_AUTH=1`；
3. 关闭注册（注册第一个账号后在设置里关）；
4. 视情况加限流 / WAF。

公网部署不属于当前 v0.1.0 验收范围，反向代理、HTTPS、限流与 WAF 尚未实现。