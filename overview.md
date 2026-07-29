# 新版代码 vs 旧版代码 — 全面对比总结

## 概述

本次更新涵盖 3 个 commit（`f74f2d1` + `2733825` + `0856991`），修改 37 个文件，+3075/-982 行代码。修复范围覆盖安全、数据完整性、生成可靠性、前端体验四个维度。

---

## 一、安全加固（旧版 → 新版）

### 1.1 项目访问控制漏洞修复

| 维度 | 旧版 | 新版 |
|------|------|------|
| 无主项目访问 | `owner_id is not None and != user.id` → owner_id 为 NULL 时所有人可访问 | `owner_id is None or != user.id` → 无主项目一律拒绝 |
| 社区小说 | 同样的 NULL 漏洞 | 同步加固 |

**影响**：旧版中 owner_id=NULL 的遗留项目可被任意已登录用户查看和操作，存在数据泄露风险。

### 1.2 密码哈希安全升级

| 维度 | 旧版 | 新版 |
|------|------|------|
| 哈希方式 | 直接 bcrypt，截断到 72 字节 | SHA-256 预处理 → bcrypt（完整密码参与运算） |
| 长密码 | >72 字节部分被丢弃 | 任意长度密码完整哈希 |
| 向后兼容 | — | `verify_password` 兼容旧哈希；`password_needs_rehash()` 登录时自动升级 |

### 1.3 网络隔离与反代加固

| 维度 | 旧版 | 新版 |
|------|------|------|
| 后端端口 | `ports: "8000:8000"` 对外暴露 | `expose: "8000"` 仅容器内部可达 |
| X-Forwarded-For | `$proxy_add_x_forwarded_for`（追加，可伪造） | `$remote_addr`（单值，不可注入） |
| 真实 IP 识别 | 无 | `set_real_ip_from` 限制信任来源为私有网段 |
| Uvicorn | 无 proxy header 处理 | `--proxy-headers --forwarded-allow-ips="*"` |

**影响**：旧版攻击者可伪造 X-Forwarded-For 绕过速率限制和 IP 去重。新版确保只有可信代理的转发头被接受。

### 1.4 DOCX 解压炸弹防护

| 维度 | 旧版 | 新版 |
|------|------|------|
| 文件数量 | 无限制 | MAX_DOCX_ENTRIES=5000 |
| 解压大小 | 无限制 | MAX_DOCX_UNCOMPRESSED_SIZE=50MB |
| 提取文本 | 无上限 | MAX_EXTRACTED_TEXT_CHARS=200000 |
| 坏文件处理 | 直接异常 | 友好错误提示 |

---

## 二、数据完整性（旧版 → 新版）

### 2.1 唯一性约束

| 维度 | 旧版 | 新版 |
|------|------|------|
| 世界观 | 同一项目可有多条 worldview 记录 | UniqueConstraint(project_id) |
| 大纲 | 同一项目可有多条 outline 记录 | UniqueConstraint(project_id) |
| 故事记忆 | 同一项目可有多条 story_memory 记录 | UniqueConstraint(project_id) |
| 迁移 | — | `8b87ca11f912` 含重复数据检测，有重复时拒绝迁移 |

**影响**：旧版并发生成可能导致同一项目出现重复记录，引发数据不一致。新版从数据库层面杜绝。

### 2.2 时间戳一致性

| 维度 | 旧版 | 新版 |
|------|------|------|
| 时间生成 | `datetime.utcnow()`（无时区信息） | `datetime.now(UTC)`（带时区，去 tz 后存储） |

### 2.3 记忆库去重

| 维度 | 旧版 | 新版 |
|------|------|------|
| 时间线事件 | `add_timeline_event` 每次追加，重复生成时产生重复条目 | 同章同事件先去重再追加 |

---

## 三、生成可靠性（旧版 → 新版）

### 3.1 LLM 截断检测

| 维度 | 旧版 | 新版 |
|------|------|------|
| 非流式 | 返回可能不完整的内容，静默处理 | 检测 `finish_reason=length` → 抛 `LLMResponseTruncatedError` |
| 流式 | 记录 WARNING 日志但继续返回 | 检测后抛 `LLMResponseTruncatedError`，调用方可处理 |
| 大纲解析 | 解析警告时仍保存不完整大纲 | 警告时拒绝保存，旧大纲保留，返回 502 让用户重试 |

**影响**：旧版用户可能收到不完整的大纲或章节而不自知。新版明确报错，避免脏数据入库。

### 3.2 章节生成并发保护

| 维度 | 旧版 | 新版 |
|------|------|------|
| 并发生成 | 无保护，同项目可同时触发多次单章/批量生成 | `_active_generation_projects` 进程内锁，拒绝重复请求 |
| token 预算 | 硬编码或不传 max_tokens | `_chapter_output_token_budget()` 动态计算（上限 32768） |
| 摘要回退 | 摘要生成失败时章节 summary 为空 | `_fallback_summary()` 提供确定性摘要 |
| 完成状态 | 粗略判断 | `_refresh_project_completion_status()` 精确统计已生成/已编辑章节数 |

### 3.3 章节内容与世界观的关联性

| 维度 | 旧版（0313fb8 前） | 新版 |
|------|------|------|
| 章节提示词 | 仅传入本章要揭示的要素 | 传入 story_arc（全局方向）+ all_element_names（合法名称列表） |
| 类型模板 | common_tropes（"废柴逆袭"等）作为内容暗示 | 移除 common_tropes，替换为"仅叙事技巧参考"提醒 |
| 身份定位 | "你是玄幻类型专家" | "你是经验丰富的网文作者"（无类型绑定） |
| 约束强度 | 弱约束（"应基于世界观"） | 强约束（"世界观即一切，不得凭空创造"） |
| 后置检查 | 无 | `_verify_worldview_alignment()` 统计要素匹配率，<20% 告警 |

---

## 四、前端体验（旧版 → 新版）

### 4.1 SSE 流可靠性

| 维度 | 旧版 | 新版 |
|------|------|------|
| 断连检测 | 无 | 检测无 complete/error 事件时提示"连接意外中断，请重试" |
| 章节选择竞态 | 切换章节时旧请求可能覆盖新请求 | `chapterSelectionRequestRef` 请求序号，过期请求结果丢弃 |
| 批量完成检测 | 无确认 | `receivedBatchComplete` 标志 |

### 4.2 依赖修复

| 维度 | 旧版 | 新版 |
|------|------|------|
| useEffect 依赖 | `[]`（空数组，仅挂载时加载） | `[projectId, totalChapters]`（切换项目时重新加载） |

---

## 五、测试覆盖（旧版 → 新版）

| 维度 | 旧版 | 新版 |
|------|------|------|
| 测试总数 | 448 | 470（+22） |
| 新增测试文件 | — | `test_chapter_safety.py`（239 行）、`test_worldview_upload_safety.py`（36 行） |
| 测试扩展 | — | `test_api_integration.py` 扩展至 1115 行（+573）、`test_llm_client.py`（+184）、`test_memory_store.py`（+96） |
| 测试场景 | — | 并发生成锁、token 预算计算、完成状态判断、DOCX 炸弹防护、截断异常、时间线去重 |

---

## 六、代码质量（旧版 → 新版）

| 维度 | 旧版 | 新版 |
|------|------|------|
| 日志 | 部分模块无 logger | chapters.py、outline.py 统一添加 `logger = logging.getLogger(__name__)` |
| 错误信息 | 暴露内部错误详情（如 `str(e)`） | 用户友好提示 + 服务端详细日志 |
| 函数职责 | `_generate_chapter_core` 承担过多职责 | 拆分出 `_fallback_summary`、`_chapter_output_token_budget`、`_refresh_project_completion_status` |
| 代码格式 | 部分长行（>120 字符） | 统一格式化（black 风格） |

---

## 验证结果

- **470 项后端测试全部通过**（3 个已知 warning，均为开发环境配置提示）
- **tsc --noEmit 零错误**
- **Alembic head = 8b87ca11f912**
- **已推送 GitHub main（0856991）**

## 服务器更新命令

```bash
cd /opt/novel-agent
git pull origin main
docker compose build --no-cache backend
docker compose up -d
# 验证迁移已执行
docker compose exec backend alembic current
# 应显示 8b87ca11f912
```
