# 满分小说 — 世界观续写 AI Agent

一款基于 AI 的小说世界观续写 Web 应用。用户上传世界观架构 + 选择网文类型，AI Agent 按渐进式策略生成网文章节内容。

## 核心功能

- **世界观管理**：手动创建 / 文档导入(AI提取) / 混合模式，支持 7 类要素模板
- **AI 生成引擎**：大纲生成 + 渐进式揭示（三阶段：引入/展开/深入）+ 7 种网文类型风格
- **章节写作**：SSE 流式生成、字数配置、一键批量生成、章节编辑
- **社区功能**：小说上传/编辑/删除、标签系统、无限滚动、点赞、共创开关
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

```bash
# 后端
cd backend
ENV_FILE=.env.dev python run.py  # 端口 8000

# 前端
cd frontend
npx vite  # 端口 5173，代理 /api 到 8000
```

### Docker 部署

#### 前置准备

```bash
# 1. 复制环境配置文件（env 文件不在 Git 中，需手动创建）
cp backend/.env.example backend/.env.prod

# 2. 编辑 .env.prod，设置以下必填项：
#    - JWT_SECRET：生成方式 → python -c "import secrets; print(secrets.token_urlsafe(64))"
#    - DATABASE_URL：PostgreSQL 连接字符串
#    - CORS_ORIGINS：你的域名

# 3. 设置环境变量
export JWT_SECRET="your-generated-secret"

# 4. 准备 TLS 证书（生产必填）
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

访问 https://your-domain.com

#### 生产部署 Checklist

- [ ] `backend/.env.prod` 已创建并配置
- [ ] `JWT_SECRET` 已设置（强随机字符串）
- [ ] `DATABASE_URL` 已配置为 PostgreSQL
- [ ] `CORS_ORIGINS` 已设置为你的域名
- [ ] TLS 证书已放入 `tls/` 目录
- [ ] `docker compose run --rm backend alembic upgrade head` 已执行
- [ ] `SENTRY_DSN` 已配置（可选但推荐）
- [ ] `JWT_SECRET` 环境变量已设置

### 环境变量

| 变量 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `JWT_SECRET` | JWT 签名密钥 | 生产必填 | 无（启动时校验） |
| `DATABASE_URL` | 数据库连接 | 生产推荐 | sqlite+aiosqlite:///./data/novel_agent.db |
| `CORS_ORIGINS` | 允许的跨域来源 | 生产必填 | localhost:5173,3000 |
| `DEBUG` | 调试模式 | 否 | false |
| `ENV_FILE` | 指定 .env 文件 | 否 | .env |
| `LLM_API_KEY` | LLM API Key | 否（可 UI 配置） | 空 |
| `SENTRY_DSN` | Sentry 错误监控 DSN | 否 | 空（禁用） |
| `RATE_LIMIT_STORAGE_URI` | 限流存储 | 否 | memory://（生产推荐 Redis） |
| `LOG_LEVEL` | 日志级别 | 否 | INFO |
| `SENTRY_SEND_PII` | Sentry 发送 PII | 否 | false |

### 环境文件

- `.env.dev` — 开发环境（DEBUG=true）
- `.env.prod` — 生产环境（DEBUG=false，需设置 JWT_SECRET）
- `.env.example` — 完整配置模板

> **注意**：`.env.dev` 和 `.env.prod` 不在 Git 版本控制中。部署前需从 `.env.example` 复制并配置。

## 测试

```bash
cd backend
ENV_FILE=.env.dev python -m pytest tests/ -v
```

## 数据库迁移

```bash
cd backend
ENV_FILE=.env.dev alembic upgrade head     # 应用迁移
ENV_FILE=.env.dev alembic revision --autogenerate -m "description"  # 生成新迁移
ENV_FILE=.env.dev alembic downgrade -1      # 回滚一个迁移
```

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
| `DATABASE_URL` | 数据库连接 | sqlite+aiosqlite:///./data/novel_agent.db | 生产推荐 PostgreSQL |
| `CORS_ORIGINS` | 允许的跨域来源 | localhost | 生产必填 |
| `DEBUG` | 调试模式 | false | — |
| `ENV_FILE` | 指定 .env 文件 | .env | — |
| `HOST` | 监听地址 | 0.0.0.0 | — |
| `PORT` | 监听端口 | 8000 | — |
| `LLM_API_KEY` | LLM API Key | 空 | 否（可 UI 配置） |
| `LLM_BASE_URL` | LLM 端点 | https://api.openai.com/v1 | — |
| `LLM_MODEL` | 模型名称 | gpt-4o | — |
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
