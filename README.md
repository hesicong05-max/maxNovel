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

```bash
# 1. 生成 JWT_SECRET
python -c "import secrets; print(secrets.token_urlsafe(64))"

# 2. 设置环境变量
export JWT_SECRET="your-generated-secret"

# 3. 启动
docker compose up -d
```

访问 http://localhost:8080

## 配置说明

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DEBUG` | 调试模式 | false |
| `DATABASE_URL` | 数据库连接 | sqlite+aiosqlite:///./data/novel_agent.db |
| `JWT_SECRET` | JWT 签名密钥（生产必填） | 无 |
| `CORS_ORIGINS` | 允许的跨域来源 | localhost:5173,3000 |
| `LLM_API_KEY` | LLM API Key | 空 |
| `SENTRY_DSN` | Sentry 错误监控 DSN | 空（禁用） |
| `RATE_LIMIT_STORAGE_URI` | 限流存储 | memory://（Redis: redis://host:6379） |
| `ENV_FILE` | 指定 .env 文件 | .env |

### 环境文件

- `.env.dev` — 开发环境（DEBUG=true）
- `.env.prod` — 生产环境（DEBUG=false，需设置 JWT_SECRET）
- `.env.example` — 完整配置模板

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
```

## GitHub

https://github.com/hesicong05-max/maxNovel
