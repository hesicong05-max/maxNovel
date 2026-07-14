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

1. **代码回滚**：`git checkout <previous-commit> && docker compose build && docker compose up -d`
2. **数据库回滚**：`docker compose run --rm backend alembic downgrade -1`
3. **数据备份**（SQLite）：`cp backend/data/novel_agent.db backup/$(date +%Y%m%d).db`
4. **数据备份**（PostgreSQL）：`pg_dump -U user novel_agent > backup/$(date +%Y%m%d).sql`

## 监控

- **Sentry**：配置 `SENTRY_DSN` 后自动启用错误监控
- **健康检查**：`GET /api/health` 返回 `{"status": "ok"}`
- **Docker 日志**：`docker compose logs -f`

## GitHub

https://github.com/hesicong05-max/maxNovel
