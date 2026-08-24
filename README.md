# 满分小说 — AI 小说创作平台

一款面向长篇创作的 AI 小说平台。现有版本支持世界观原文提取、模块化设定仓库，以及旧项目基于历史章节安排继续写作；篇章—章节规划、伏笔、时间线和一致性闭环正按里程碑建设。

## 核心功能

- **世界观管理**：原文导入、对象级 AI 提取、人工审核，以及 15 类内置设定模块
- **设定仓库**：分类检索、版本、来源、关系、冲突提示、重复合并和可恢复归档
- **兼容章节写作**：旧项目可继续 SSE 流式生成、字数配置、批量生成、章节编辑和导出
- **现有社区原型**：小说上传、编辑、标签、点赞和共创开关；核心 Beta 前不扩张社区能力
- **用户系统**：JWT 认证、注册/登录、路由守卫、API 访问控制
- **导出**：TXT / Markdown 格式

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | React + TypeScript + Vite |
| 后端 | Python FastAPI + SQLAlchemy 异步 |
| 数据库 | SQLite (开发) / PostgreSQL (生产) |
| AI | OpenAI 兼容 API (支持 6 个预置服务商) |
| 部署 | Docker + docker-compose + nginx |

## 快速开始

### 开发环境

要求 Python 3.13（最低 3.11）和 Node.js 22。

```bash
# 后端依赖与本地配置
python3.13 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install -r backend/requirements.txt
cp backend/.env.example backend/.env.dev
# 编辑 backend/.env.dev：设置 DEBUG=true；LLM_API_KEY 可留空

cd backend
ENV_FILE=.env.dev python -m alembic upgrade head
ENV_FILE=.env.dev python run.py  # http://localhost:8000

# 另开终端启动前端
cd frontend
npm ci
npm run dev  # http://localhost:5173，代理 /api 到 8000
```

### 非生产最终 Demo

最终 Demo 使用独立 SQLite、固定技术模拟和失败关闭的环境门禁，不需要真实 LLM。请严格按照 [最终 Demo 本地运行手册](docs/demo-runbook.md) 创建新的 run-id；不要复用开发或生产数据库。

### Docker 部署

#### 前置准备

```bash
# 1. Compose 插值变量：根目录 .env（不提交）
cp .env.example .env

# 2. 后端生产变量：backend/.env.prod（不提交）
cp backend/.env.example backend/.env.prod

# 3. 编辑根目录 .env：设置 DB_USER、DB_PASSWORD、JWT_SECRET
#    编辑 backend/.env.prod：设置 DEBUG=false、CORS_ORIGINS 和可选 LLM 配置
#    DATABASE_URL 由 docker-compose.yml 根据数据库变量生成

# 4. 如由本仓库 nginx 终止 TLS，再准备证书
mkdir -p tls
# 将你的证书文件放入 tls/ 目录：
#   tls/fullchain.pem  — 证书链
#   tls/privkey.pem    — 私钥
# 推荐使用 Let's Encrypt 免费证书：https://letsencrypt.org/

# 5. 运行数据库迁移
docker compose run --rm backend alembic upgrade head

# 6. 启动
docker compose up -d
```

访问 `http://localhost:8080`，或通过已配置 TLS 的反向代理访问生产域名。

#### 生产部署 Checklist

- [ ] 根目录 `.env` 已从 `.env.example` 创建
- [ ] `backend/.env.prod` 已从 `backend/.env.example` 创建
- [ ] 根目录 `.env` 的 `DB_USER`、`DB_PASSWORD`、`JWT_SECRET` 已设置
- [ ] `CORS_ORIGINS` 已设置为你的域名
- [ ] `docker compose run --rm backend alembic upgrade head` 已执行
- [ ] `SENTRY_DSN` 已配置（可选但推荐）

### 环境变量

| 变量 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `JWT_SECRET` | JWT 签名密钥 | 生产必填 | 无（启动时校验） |
| `DATABASE_URL` | 数据库连接 | 生产推荐 | sqlite+aiosqlite:///./data/novel_agent.db |
| `JSON_PREFLIGHT_HMAC_KEY` | 历史资料只读预检的脱敏密钥（至少 32 字节，不复用 JWT） | 仅 PostgreSQL 预检必填 | 空 |
| `LEGACY_JSON_WRITES_FROZEN` | 历史资料维护期间冻结受影响的项目写入 | 否 | false |
| `LEGACY_JSON_MAINTENANCE_EVENT_ID` | 可向用户展示的非敏感维护事件编号 | 否 | BUG-002B |
| `LEGACY_JSON_MAINTENANCE_RETRY_AFTER` | 客户端重试提示秒数（30–3600） | 否 | 60 |
| `CORS_ORIGINS` | 允许的跨域来源 | 生产必填 | localhost:5173,3000 |
| `DEBUG` | 调试模式 | 否 | false |
| `ENV_FILE` | 指定 .env 文件 | 否 | .env |
| `LLM_API_KEY` | LLM API Key | 否（可 UI 配置） | 空 |
| `SENTRY_DSN` | Sentry 错误监控 DSN | 否 | 空（禁用） |
| `RATE_LIMIT_STORAGE_URI` | 限流存储 | 否 | memory://（生产推荐 Redis） |
| `LOG_LEVEL` | 日志级别 | 否 | INFO |
| `SENTRY_SEND_PII` | Sentry 发送 PII | 否 | false |

### 环境文件

- 根目录 `.env.example`：Docker Compose 插值变量模板；复制为根目录 `.env`
- `backend/.env.example`：后端完整模板；开发复制为 `backend/.env.dev`，生产复制为 `backend/.env.prod`
- `frontend/.env.example`：仅在前后端分开部署时配置前端构建变量
- 根目录 `.env.prod`：历史兼容模板，不会被 Docker Compose 自动读取；新部署不要把密钥写入该受版本控制文件

> 不要把密钥写入任何 `*.example` 或受版本控制的文件。Docker Compose 默认只自动读取根目录 `.env`。

## 全量验证

安装上述后端和前端依赖后，在仓库根目录只需运行：

```bash
PYTHON_BIN=backend/.venv/bin/python ./scripts/verify.sh
```

该命令依次运行 `backend/tests` 全目录、高严重度 Bandit 安全门禁、前端类型检查、单元测试和生产构建。CI 使用相同的测试范围与安全阈值。

### 设定库只读 API

`DEV-003A` 提供旧世界观到统一设定模型的无副作用预览：

- `GET /api/projects/{project_id}/lore/elements`
- `GET /api/projects/{project_id}/lore/elements/{element_id}`
- `GET /api/projects/{project_id}/lore/elements/{element_id}/sources`
- `GET /api/projects/{project_id}/lore/elements/{element_id}/versions`

这些接口需要登录和项目所有权，只读取现有世界观，不会写入新表、切换事实源或修改项目 JSON 文件。

## 数据库迁移

```bash
cd backend
ENV_FILE=.env.dev alembic upgrade head     # 应用迁移
ENV_FILE=.env.dev alembic revision --autogenerate -m "description"  # 生成新迁移
ENV_FILE=.env.dev alembic downgrade -1      # 回滚一个迁移
```

### 历史 JSON 存储只读预检

`BUG-002A` 命令只检查历史 PostgreSQL Text/JSON 数据形态，不执行迁移、修复或
写入。连接地址仅从 `ENV_FILE` 对应配置读取，不接受命令行连接串；报告不会输出
数据库连接信息、原始记录 ID 或小说内容。

```bash
cd backend

# 在 backend/.env.prod 中配置独立的 JSON_PREFLIGHT_HMAC_KEY（至少 32 字节）
ENV_FILE=.env.prod python -m app.commands.json_preflight \
  --format json \
  --environment-label 生产只读检查 \
  --output ./preflight-report.json
```

输出文件使用 `0600` 权限原子写入，且拒绝覆盖任何已有文件或符号链接。也可使用
`--format text` 输出适合 80 列终端的纵向摘要。退出码为：

- `0`：SQLite 明确不适用；
- `2`：PostgreSQL 数据形态通过但后续维护、备份与恢复证据尚未完成，或存在
  需要人工确认的兼容旧结构；
- `3`：发现阻断项；
- `4`：配置、schema、版本或查询错误。

PostgreSQL 的 `data_shape_status` 可以为 `PASS`，但在维护、锁、容量、备份与
恢复门禁完成前，`overall_status` 仍为 `REVIEW_REQUIRED`，命令返回 `2`。
任何结果都不构成真实数据转换批准。

### 历史资料维护写入冻结

`BUG-002B1` 提供默认关闭的部署级写入冻结。启用后，受历史 JSON 字段影响的
世界观、历史章节安排兼容字段、章节、故事记忆写入以及项目级联删除统一返回 HTTP `503`、
`PROJECT_WRITE_FROZEN` 和 `Retry-After`；已经建立的 SSE 在最终保存前再次
检查，并以同一安全错误对象终止。读取、世界观文件解析/导入预览、认证、社区和
项目创建/基本信息编辑保持可用。

```dotenv
LEGACY_JSON_WRITES_FROZEN=true
LEGACY_JSON_MAINTENANCE_EVENT_ID=BUG-002B
LEGACY_JSON_MAINTENANCE_RETRY_AFTER=60
```

维护状态可通过 `GET /api/version/maintenance` 检查。该开关在进程启动时加载，
不是分布式锁；启用前必须一次性把配置部署到全部实例并排空旧实例与活跃生成流，
再执行第二次只读预检。滚动部署中只冻结部分实例不能作为迁移门禁。恢复写入时
同样需要确保全部实例配置一致。此功能只建立维护边界，不会执行迁移、修复或
真实数据转换。

### Docker 中执行迁移

```bash
# 首次部署
docker compose run --rm backend alembic upgrade head

# 回滚
docker compose run --rm backend alembic downgrade -1
```

## 回滚方案

### 代码回滚

```bash
# 回退到上一个提交
git checkout <previous-commit>
docker compose build && docker compose up -d

# 验证服务正常
curl https://your-domain.com/api/health
```

### 数据库回滚

```bash
# 回退一个 Alembic 迁移
docker compose run --rm backend alembic downgrade -1

# 回退到特定迁移版本
docker compose run --rm backend alembic downgrade <revision_id>
```

### 数据备份

```bash
# SQLite（开发环境）
cp backend/data/novel_agent.db backup/$(date +%Y%m%d_%H%M%S).db

# PostgreSQL（生产环境）
pg_dump -U user -h localhost novel_agent | gzip > backup/$(date +%Y%m%d_%H%M%S).sql.gz

# 恢复 PostgreSQL 备份
gunzip -c backup/20260714_120000.sql.gz | psql -U user novel_agent
```

## 数据库备份策略

### 自动化备份（推荐）

```bash
# 添加到 crontab，每天凌晨 3 点自动备份
# PostgreSQL:
0 3 * * * pg_dump -U user novel_agent | gzip > /backup/novel_$(date +\%Y\%m\%d).sql.gz

# 保留最近 30 天的备份，自动清理旧备份
0 4 * * * find /backup -name "novel_*.sql.gz" -mtime +30 -delete
```

### 备份策略

| 项目 | 频率 | 保留 | 存储 |
|------|------|------|------|
| 全量备份 | 每日 03:00 | 30 天 | 本地 + 异地 |
| WAL 归档 | 实时 | 7 天 | 本地 |
| 测试恢复 | 每月 | — | 验证备份可用性 |

## 监控与告警

### Sentry 错误监控

1. 注册 Sentry 账号：https://sentry.io/
2. 创建项目（选择 FastAPI + React）
3. 获取 DSN
4. 配置环境变量：
   ```bash
   # 后端
   SENTRY_DSN=https://xxx@sentry.io/xxx
   SENTRY_TRACES_SAMPLE_RATE=0.1
   SENTRY_SEND_PII=false  # 生产环境不建议发送 PII

   # 前端（构建时注入）
   VITE_SENTRY_DSN=https://xxx@sentry.io/xxx
   ```

### 健康检查

- **后端**：`GET /api/health` → `{"status": "ok"}`
- **Docker**：自动健康检查（30s 间隔，3 次失败标记为 unhealthy）
- **docker-compose**：`docker compose ps` 查看健康状态

### 日志

```bash
# 查看实时日志
docker compose logs -f

# 查看特定服务
docker compose logs -f backend
docker compose logs -f frontend

# 日志自动轮转（已配置）
# 每个 container 最多 3 个日志文件，每个 10MB
```

### 告警建议

| 指标 | 阈值 | 告警方式 |
|------|------|---------|
| 后端 5xx 错误率 | > 1% | Sentry 自动告警 |
| 响应时间 P99 | > 5s | Sentry Performance |
| 容器健康状态 | unhealthy | Docker healthcheck + 外部监控 |
| 磁盘空间 | > 80% | 系统监控（如 Prometheus + Grafana） |
| 数据库连接失败 | > 0 | Sentry + 日志告警 |

## 环境变量检查清单

### 必填项（生产环境）

- [ ] `JWT_SECRET` — 强随机字符串（`python -c "import secrets; print(secrets.token_urlsafe(64))"`）
- [ ] `DATABASE_URL` — PostgreSQL 连接字符串
- [ ] `CORS_ORIGINS` — 你的生产域名

### 推荐配置

- [ ] `SENTRY_DSN` — Sentry 错误监控
- [ ] `VITE_SENTRY_DSN` — 前端 Sentry
- [ ] `RATE_LIMIT_STORAGE_URI` — Redis 连接（多进程部署）
- [ ] `LOG_LEVEL=WARNING` — 生产环境日志级别

### 可选项

- [ ] `LLM_API_KEY` — 可通过 UI 配置（管理员）
- [ ] `LLM_BASE_URL` — 自定义 LLM 端点
- [ ] `LLM_MODEL` — 模型名称
- [ ] `SENTRY_SEND_PII` — 默认 false，生产不建议开启
- [ ] `SENTRY_TRACES_SAMPLE_RATE` — 性能采样率（0.1 = 10%）

### 完整环境变量表

| 变量 | 说明 | 默认值 | 必填 |
|------|------|--------|------|
| `JWT_SECRET` | JWT 签名密钥 | 无 | 生产必填 |
| `DATABASE_URL` | 数据库连接 | sqlite+aiosqlite:///./data/novel_agent.db | Compose 自动生成；其他生产部署必填 |
| `CORS_ORIGINS` | 允许的跨域来源 | localhost | 生产必填 |
| `DEBUG` | 调试模式 | false | — |
| `ENV_FILE` | 指定 .env 文件 | .env | — |
| `HOST` | 监听地址 | 0.0.0.0 | — |
| `PORT` | 监听端口 | 8000 | — |
| `LLM_API_KEY` | LLM API Key | 空 | 否（可 UI 配置） |
| `LLM_BASE_URL` | LLM 端点 | https://api.deepseek.com/v1 | — |
| `LLM_MODEL` | 模型名称 | deepseek-chat | — |
| `LLM_MAX_TOKENS` | 最大 token 数 | 4096 | — |
| `LLM_TEMPERATURE` | 生成温度 | 0.8 | — |
| `MAX_UPLOAD_SIZE` | 上传限制 | 10485760 (10MB) | — |
| `RATE_LIMIT_DEFAULT` | 默认限流 | 60/minute | — |
| `RATE_LIMIT_LLM` | LLM 端点限流 | 10/minute | — |
| `RATE_LIMIT_STORAGE_URI` | 限流存储 | memory:// | 生产推荐 Redis |
| `JWT_ALGORITHM` | JWT 算法 | HS256 | — |
| `JWT_EXPIRE_DAYS` | Token 有效期 | 7 | — |
| `SENTRY_DSN` | Sentry DSN | 空 | 推荐 |
| `SENTRY_TRACES_SAMPLE_RATE` | 性能采样 | 0.1 | — |
| `SENTRY_SEND_PII` | 发送 PII | false | — |
| `LOG_LEVEL` | 日志级别 | INFO | 生产推荐 WARNING |
| `VITE_SENTRY_DSN` | 前端 Sentry | 空 | 推荐 |
| `VITE_API_BASE_URL` | API 基础路径 | /api | — |

## GitHub

https://github.com/hesicong05-max/maxNovel
