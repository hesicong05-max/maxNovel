# 负载测试指南

## 概述

使用 [Locust](https://locust.io/) 对满分小说后端进行负载测试，覆盖以下场景：

- 用户注册/登录
- 项目创建/列表/进度
- 世界观设置
- 章节列表/字数配置
- 社区浏览/详情/点赞
- 导出功能
- **SSE 流式章节生成**（最重负载场景）

## 安装

```bash
pip install locust
```

## 运行

### 1. Web UI 模式（推荐）

```bash
cd backend
locust -f load_tests/locustfile.py --host=http://localhost:8000
```

打开 http://localhost:8089 配置并发用户数和生成速率。

### 2. 无头模式

```bash
# 烟雾测试（10 并发，1/s 生成，30秒）
locust -f load_tests/locustfile.py --host=http://localhost:8000 \
  --headless -u 10 -r 1 -t 30s

# 正常负载（50 并发，5/s 生成，2分钟）
locust -f load_tests/locustfile.py --host=http://localhost:8000 \
  --headless -u 50 -r 5 -t 120s

# 压力测试（200 并发，10/s 生成，3分钟）
locust -f load_tests/locustfile.py --host=http://localhost:8000 \
  --headless -u 200 -r 10 -t 180s

# 峰值测试（500 并发，50/s 生成，1分钟）
locust -f load_tests/locustfile.py --host=http://localhost:8000 \
  --headless -u 500 -r 50 -t 60s
```

### 3. SSE 专项测试

```bash
locust -f load_tests/locustfile.py:SSEChapterGenerationUser \
  --host=http://localhost:8000 --headless -u 20 -r 2 -t 60s
```

## 推荐测试场景

| 场景 | 用户数 | 生成速率 | 持续时间 | 目标 |
|------|--------|---------|---------|------|
| 烟雾测试 | 10 | 1/s | 30s | 验证基本功能 |
| 正常负载 | 50 | 5/s | 2min | 验证日常承载能力 |
| 压力测试 | 200 | 10/s | 3min | 找到性能瓶颈 |
| 峰值测试 | 500 | 50/s | 1min | 验证突发流量处理 |
| SSE专项 | 20 | 2/s | 60s | 验证流式生成并发 |

## 性能基线

以下为参考基线（SQLite + 单进程，实际以 PostgreSQL 为准）：

| 指标 | 目标值 | 说明 |
|------|--------|------|
| P50 响应时间 | < 100ms | 中位数响应时间 |
| P95 响应时间 | < 500ms | 95% 请求响应时间 |
| P99 响应时间 | < 2000ms | 99% 请求响应时间 |
| 成功率 | > 95% | 非 5xx/429 响应比例 |
| 错误率 | < 5% | 5xx + 429 比例 |
| RPS | > 100 | 每秒请求数（50并发时） |

## 输出分析

测试结束后 Locust 会输出汇总信息：
- **Total requests**: 总请求数
- **Total failures**: 失败请求数
- **Avg response time**: 平均响应时间
- **Max response time**: 最大响应时间
- **Success rate**: 成功率

Web UI 模式还提供：
- 实时 RPS 图表
- 响应时间分位数图
- 失败详情列表
- CSV 导出
