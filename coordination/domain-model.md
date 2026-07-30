# `DEV-002` 统一领域模型、兼容迁移与回滚方案

## 0. 基本信息

- 任务编号：`DEV-002`
- 文档版本：v1.0
- 日期：2026-07-30
- 当前状态：`APPROVED`
- 临时主负责人：最高管理者
- 协作 Agent：网站设计者 `/root/des001_ia`
- 依据：`coordination/product-scope.md`、`coordination/design-setting-library.md`
- 影响范围：后端领域模型、API、Alembic 迁移、旧 JSON 兼容读取和前端设定库
- 非范围：本任务不编写迁移或业务代码，不修改历史迁移，不删除旧表、旧 JSON 或核心组件

## 1. 结论摘要

M1 采用新增关系模型、按项目迁移、单一事实源切换的渐进方案，不重写现有系统。

核心决定：

1. 新增统一设定类型、设定、来源、关系、内容版本、章节绑定、伏笔动作和合并重定向模型；
2. 增加内置“地点”类型，旧 `geography` 迁移为地点；“场景”只表示带时间、参与者和目的的叙事场面；
3. 设定内容、关系和章节绑定分别版本化；恢复设定内容不会静默恢复关系或章节计划；
4. 内容版本快照包括名称、摘要、类型、结构化内容及当时的类型定义版本；
5. 确认、归档、合并属于生命周期事件，不随内容版本恢复；
6. 列表使用服务端 cursor 分页，普通用户不接触稳定 ID、schema 或并发令牌；
7. 合并采用“影响预览令牌 + 单事务提交 + 旧 ID 重定向”；
8. 旧 JSON 采用双读、单写、派生兼容投影；按项目完成校验后切换，不做无控制双写；
9. SQLite 与 PostgreSQL 使用同一逻辑模型，不依赖 PostgreSQL 专属 JSON 查询作为正确性前提；
10. M1 只提供字段级旧值/新值差异，不实现字符级或语义差异。

## 2. 当前模型及迁移问题

现有事实分散在：

- `worldviews`：七类 JSON 数组、原文、来源和 `parsed_elements`；
- `outlines`：章节计划和 `reveal_plan` JSON；
- `chapters`：正文及 `revealed_elements`；
- `story_memories`：已揭示要素、角色状态、伏笔、时间线和章节摘要；
- `backend/data/projects/{project_id}`：世界观与大纲的派生 JSON 文件。

当前主要问题：

- 数组索引和名称承担身份，重名或改名会破坏引用；
- 世界观、章节计划和记忆分别保存同一概念，计划与实际容易混写；
- 整个 JSON 大对象覆盖保存，无法做单项并发保护和恢复；
- 来源只有项目级粗粒度状态；
- 没有自定义类型定义、关系约束、合并重定向和独立历史；
- `geography` 表示地点，不能直接映射为产品术语中的叙事“场景”。

数据库仍是事实源。项目文件继续作为可查看的派生文档，不独立承载新事实。

## 3. 类型体系

### 3.1 内置类型

每个项目创建以下内置类型定义：

- `world`
- `character`
- `location`
- `scene`
- `faction`
- `item`
- `conflict`
- `event`
- `foreshadow`
- `rule`

其中 `location` 是为兼容旧 `geography` 和避免地点/场景语义混淆而增加的基础类型。它不改变平台定位，也不替代用户要求的“场景”。

### 3.2 `SettingType`

建议表：`setting_types`

核心字段：

| 字段 | 说明 |
|---|---|
| `id` | 32 位稳定 ID |
| `project_id` | 所属项目 |
| `key` | 项目内稳定内部 key，创建后不可修改 |
| `display_name` | 用户可修改的显示名称 |
| `description` | 类型用途说明 |
| `is_builtin` | 是否为内置类型 |
| `schema_revision` | 当前字段定义版本，单调递增 |
| `field_schema` | JSON 字段定义 |
| `status` | `active` / `archived` |
| `created_at` / `updated_at` | 时间 |

约束：

- `UNIQUE(project_id, key)`；
- 内置类型的 `key` 和核心字段类型不可变；
- 自定义类型已有实例后，只允许新增字段、修改显示名/帮助/排序或停用字段；
- 不允许静默改变稳定字段 key 或字段数据类型；
- 每次字段定义变化新增 `setting_type_revisions` 快照。

建议表：`setting_type_revisions`

- `type_id`
- `revision`
- `display_name`
- `field_schema`
- `change_summary`
- `created_at`

唯一约束：`UNIQUE(type_id, revision)`。

历史设定版本使用当时的 `schema_revision` 渲染，不能用最新字段定义错误解释旧值。

## 4. 核心实体

### 4.1 `SettingElement`

建议表：`setting_elements`

| 字段 | 说明 |
|---|---|
| `id` | 项目内外均稳定的 32 位 ID |
| `project_id` | 所属项目 |
| `type_id` | `SettingType` |
| `name` | 名称 |
| `normalized_name` | 搜索和候选去重使用，不作为唯一身份 |
| `summary` | 一句话摘要 |
| `payload` | 结构化字段值 |
| `payload_schema_revision` | 写入时的类型定义版本 |
| `confirmation_status` | `candidate` / `confirmed` / `rejected` |
| `lifecycle_status` | `active` / `archived` / `merged` |
| `content_version` | 当前内容版本号 |
| `lock_version` | 乐观并发版本，任何可写状态变化时递增 |
| `created_at` / `updated_at` | 时间 |

规则：

- 名称可以重复，界面通过类型、摘要、来源和关系消歧；
- `candidate` 不得作为正式生成依据；
- `merged` 记录只用于重定向，不能继续编辑；
- 归档不等于删除；M1 不提供永久删除设定；
- `payload` 必须通过对应 `SettingType` revision 校验。

索引：

- `(project_id, lifecycle_status, updated_at, id)`；
- `(project_id, type_id, lifecycle_status, updated_at, id)`；
- `(project_id, confirmation_status, updated_at, id)`；
- `(project_id, normalized_name, id)`。

### 4.2 `ElementSource`

建议表：`element_sources`

一个设定允许多个来源。

字段：

- `id`
- `project_id`
- `element_id`
- `source_kind`：`manual` / `document_import` / `ai_suggestion` / `system_extract` / `migration`
- `source_ref`：导入批次、文档或生成运行 ID
- `locator`：页码、段落、字符区间等 JSON 定位
- `excerpt_hash`：来源片段哈希，不强制重复保存全文
- `is_primary`
- `created_at`

索引：

- `(project_id, element_id, created_at)`；
- `(project_id, source_kind, source_ref)`。

来源不等于确认；确认状态只保存在 `SettingElement`。

### 4.3 `ElementVersion`

建议表：`element_versions`

不可变快照包含：

- `element_id`
- `version_no`
- `type_id`
- `type_schema_revision`
- `name`
- `summary`
- `payload`
- `change_reason`
- `source_id`（可空）
- `created_by`
- `created_at`

唯一约束：`UNIQUE(element_id, version_no)`。

恢复规则：

- 恢复旧版本会创建新的版本号；
- 只恢复类型、名称、摘要和结构化内容；
- 不恢复确认、归档、合并状态；
- 不恢复来源历史；
- 不恢复关系、章节绑定和伏笔动作；
- 界面必须明确显示“仅恢复设定内容”及不会变化的项目。

M1 差异：

- 按字段展示旧值与新值；
- JSON 对象按稳定字段 key 比较；
- 不实现字符级或语义差异。

### 4.4 `ElementStateEvent`

建议表：`element_state_events`

用于审计不属于内容版本的生命周期变化：

- `element_id`
- `event_kind`：`confirm` / `reject` / `archive` / `restore_archive` / `merge`
- `before_state`
- `after_state`
- `actor_id`
- `created_at`

确认、归档或合并不通过恢复内容版本回滚。

### 4.5 `ElementRelation`

建议表：`element_relations`

字段：

- `id`
- `project_id`
- `source_element_id`
- `target_element_id`
- `relation_key`
- `forward_label`
- `reverse_label`
- `description`
- `metadata`
- `status`：`active` / `archived`
- `version_no`
- `lock_version`
- `created_at` / `updated_at`

约束：

- 两端必须属于同一项目；
- 默认禁止自环；明确允许的关系类型可例外；
- `UNIQUE(project_id, source_element_id, target_element_id, relation_key)`；
- 已归档的同语义关系重新创建时恢复原记录并新增版本，不创建冲突副本。

索引：

- `(project_id, source_element_id, status, relation_key)`；
- `(project_id, target_element_id, status, relation_key)`。

建议表：`element_relation_versions`

每次创建、修改、归档和恢复都保存不可变快照。界面单独显示“关系变更”，不混入内容历史。

M1 以当前设定的自然语言邻接列表为验收门禁；全项目关系图是增强项，不能阻塞核心交付。

### 4.6 `ChapterBinding`

建议表：`chapter_bindings`

字段：

- `id`
- `project_id`
- `chapter_id`：引用稳定章节 ID，不用章节序号作外键
- `element_id`
- `binding_kind`：`appearance` / `reference` / `reveal`
- `fact_kind`：`planned` / `actual`
- `status`：`active` / `superseded`
- `evidence`：实际正文位置或提取依据
- `version_no`
- `lock_version`
- `created_at` / `updated_at`

约束：

- 项目、章节和设定必须属于同一项目；
- 一个设定在项目内最多有一个有效的 `planned + appearance`；
- 实际首次出现由已确认正文和证据产生，不能由计划静默覆盖；
- M1 API 可读写基础绑定，但设定库只显示摘要；完整计划编辑在 M2 启用。

建议表：`chapter_binding_versions`，记录绑定创建、修改、失效和恢复。界面单独显示“出现记录”。

索引：

- `(project_id, element_id, fact_kind, binding_kind, status)`；
- `(project_id, chapter_id, fact_kind, status)`。

### 4.7 `ForeshadowAction`

建议表：`foreshadow_actions`

伏笔本身是 `type=foreshadow` 的 `SettingElement`，动作单独记录：

- `id`
- `project_id`
- `foreshadow_element_id`
- `chapter_id`
- `action_kind`：`plant` / `strengthen` / `resolve`
- `fact_kind`：`planned` / `actual`
- `status`：`active` / `superseded`
- `evidence`
- `version_no`
- `created_at` / `updated_at`

规则：

- 可以有多个埋入和强化动作；
- 只能有一个当前有效的实际回收结果；
- 完整生命周期编辑在 M2，M1 仅保证模型和摘要可用。

### 4.8 `ElementRedirect`

建议表：`element_redirects`

字段：

- `project_id`
- `old_element_id`
- `canonical_element_id`
- `reason`：`merge` / `migration`
- `created_at`

约束：

- `old_element_id` 唯一；
- 两个元素必须属于同一项目；
- 禁止循环重定向；
- API 读取旧 ID 时返回规范对象并携带 `redirected_from`，界面显示“已合并到……”。

## 5. 合并事务

### 5.1 预览

`POST /api/projects/{project_id}/lore/merge-preview`

输入：

- 保留元素 ID；
- 待合并元素 ID；
- 字段选择；
- `expected_version`。

输出：

- 字段冲突；
- 来源数；
- 关系影响；
- 章节绑定影响；
- 伏笔动作影响；
- 将产生的重定向；
- 有效期较短的 `preview_token`。

服务端保存或签名预览时包含参与元素、关系和绑定版本摘要。

### 5.2 提交

`POST /api/projects/{project_id}/lore/merge-commit`

在单个数据库事务中：

1. 校验 `preview_token` 未过期；
2. 校验所有参与对象版本未变化；
3. 生成保留元素的新内容版本；
4. 合并来源；
5. 重定向并去重关系、章节绑定和伏笔动作；
6. 将旧元素标为 `merged`；
7. 写入 `ElementRedirect` 和状态事件；
8. 提交事务。

预览过期或数据变化返回 `409`。前端保留字段选择，重新生成影响预览后再提交。

## 6. API 边界

推荐基础路径：`/api/projects/{project_id}/lore`。

### 6.1 列表

`GET /elements`

参数：

- `cursor`
- `limit`：桌面默认 30，移动默认 20，最大 100
- `type`
- `confirmation_status`
- `source_kind`
- `has_relation`
- `has_chapter_binding`
- `lifecycle_status`
- `q`
- `sort`
- `direction`

响应：

- `items`
- `next_cursor`
- `previous_cursor`（可选）
- `has_more`
- 当前筛选统计（可独立端点缓存）

cursor 是包含排序值和稳定 ID 的不透明令牌。桌面上一页由前端维护游标栈，移动使用“加载更多”；M1 不承诺任意页码跳转。

### 6.2 写入与并发

- `POST /elements`
- `GET /elements/{id}`
- `PATCH /elements/{id}`
- `POST /elements/{id}/archive`
- `POST /elements/{id}/restore-archive`
- `GET /elements/{id}/versions`
- `GET /elements/{id}/versions/{version_no}`
- `POST /elements/{id}/restore-version`
- 关系、来源、绑定和合并使用独立子资源。

所有修改请求必须包含 `expected_version`。服务端使用：

```sql
UPDATE ... SET lock_version = lock_version + 1
WHERE id = :id AND lock_version = :expected_version
```

未更新任何行时返回 `409`，并返回最新摘要，不静默覆盖。

### 6.3 移动草稿

M1 不新增服务端草稿表。

前端草稿键至少包含：

- 用户 ID；
- 项目 ID；
- 设定 ID 或 `new`；
- 基础 `lock_version`。

草稿仅保存在当前设备，保存成功后清除；退出登录时清除；默认 7 天过期。恢复草稿前先获取服务器最新版本，版本不一致时进入冲突处理。

## 7. 旧数据迁移

### 7.1 迁移状态

建议在 `projects` 增加：

- `lore_storage_mode`：`legacy` / `migrating` / `relational` / `rollback_pending`
- `lore_migration_version`

建议表：`project_lore_migrations`

- `project_id`
- `migration_version`
- `status`
- `source_checksum`
- `result_checksum`
- `counts`
- `validation_errors`
- `started_at` / `completed_at`

### 7.2 类别映射

| 旧数据 | 新类型 |
|---|---|
| `characters` | `character` |
| `geography` | `location` |
| `factions` | `faction` |
| `power_system` | `rule`，保留 `legacy_category=power_system` |
| `history` | `event` |
| `conflicts` | `conflict` |
| `special_settings` | `rule` 或按内容建立自定义类型，保留旧类别 |
| `StoryMemory.foreshadows` | `foreshadow` + `ForeshadowAction` |
| `StoryMemory.timeline` | `event` 或现有事件的实际记录 |

旧数据没有可靠“场景”对象时不得自动猜测生成场景。

### 7.3 稳定 ID

优先级：

1. 保留项目内唯一且格式有效的旧 `parsed_elements.id`；
2. 若缺失或冲突，生成确定性迁移 ID；
3. 持久化旧类别、数组位置、名称和旧 ID 到迁移映射；
4. 后续改名不改变 ID。

不能继续依赖名称匹配建立正式关系。

### 7.4 双读与单写

读取顺序由项目的 `lore_storage_mode` 决定：

- `legacy`：读取现有 JSON；
- `migrating`：用户只读，后台比较旧数据与新数据；
- `relational`：只从新模型读取；
- `rollback_pending`：暂停写入并确认兼容投影完整。

写入始终只进入当前事实源。切换到新模型后，旧 `worldviews`、`outlines` 和 `story_memories` 只接收由新模型生成的派生兼容投影，不作为第二事实源。

### 7.5 按项目迁移步骤

1. 创建新增表和索引，不修改旧表；
2. 获取项目迁移锁，状态改为 `migrating`；
3. 读取数据库旧 JSON；文件副本只用于校验，不覆盖数据库；
4. 生成类型、设定、来源、关系和初始版本；
5. 映射大纲揭示计划、章节实际揭示、伏笔和时间线；
6. 验证数量、ID、名称、类型、来源、关系和章节引用；
7. 生成兼容投影并比较校验和；
8. 事务提交新数据；
9. 项目切换为 `relational`；
10. 保留旧数据和迁移记录，不删除。

### 7.6 校验门禁

切换前必须满足：

- 每个旧数组条目都有迁移结果或明确的人工处理记录；
- 稳定 ID 项目内唯一；
- 所有关系两端存在且同属项目；
- 所有章节引用能解析到稳定章节和设定；
- 来源记录数量和类型正确；
- 确认状态没有把 AI 候选误当正式事实；
- 新模型可生成与旧核心字段等价的兼容投影；
- 文件副本与数据库事实源差异已报告。

### 7.7 回滚

迁移切换前失败：

- 回滚新增项目数据；
- 状态恢复 `legacy`；
- 旧表和文件不变。

切换后尚无新写入：

- 校验兼容投影后恢复 `legacy`；
- 新表记录保留但停用，便于排查。

切换后已有新写入：

- 先冻结项目写入；
- 从新模型生成最新兼容投影；
- 完整校验后才允许恢复旧读路径；
- 无法无损投影的自定义字段或关系必须导出并提示，不得静默丢失；
- 生产回滚由专项迁移执行，不直接删除新表或历史。

## 8. SQLite 与 PostgreSQL

- 使用 SQLAlchemy 通用 `JSON`、字符串 ID、显式外键和普通复合索引；
- 正确性不依赖 JSONB、数组、全文索引或数据库触发器；
- SQLite 外键测试必须显式开启；
- SQLite 结构变更使用 Alembic batch mode；
- PostgreSQL 可在不改变 API 的前提下增加全文/JSONB 表达式索引；
- 多行合并和项目切换必须在单事务中完成；
- SQLite 采用短写事务，避免大项目长时间锁库；
- 关系两端同项目等跨行约束由服务层校验，并通过集成测试保证。

## 9. 性能与容量

目标基线：200 章、1,000 个设定、10,000 条关系/绑定。

要求：

- 所有列表服务端分页，禁止全量载入；
- 关系详情只查询当前要素的邻接关系；
- 分类计数使用独立聚合查询和短时缓存，不扫描前端列表；
- 版本列表分页，版本快照按需加载；
- 生成上下文按章节计划和关系查询相关设定，不加载整个项目；
- 查询必须带 `project_id`，防止跨租户扫描；
- cursor 排序字段后总是追加稳定 `id`；
- M1 性能验证记录 p50/p95，并检查关键查询计划。

建议验收目标：

- 1,000 要素常用筛选列表 p95 小于 500ms（本地基准环境）；
- 单要素邻接关系 p95 小于 300ms；
- 30 条分页响应不包含完整历史和全部来源正文；
- 10,000 条关系条件下不执行全图默认加载。

## 10. 测试与验证矩阵

### 模型

- 内置与自定义类型创建；
- 稳定字段 key 和 schema revision；
- 名称重复但 ID 独立；
- 跨项目关系、绑定和重定向被拒绝；
- 关系循环和重定向循环被拒绝；
- 计划首次出现唯一约束；
- 伏笔只能有一个有效实际回收。

### 版本与并发

- 内容修改生成新版本；
- 恢复旧内容生成新当前版本；
- 恢复不改变确认、归档、关系和章节绑定；
- 类型旧 revision 可正确渲染；
- 过期 `expected_version` 返回 409；
- 关系和绑定独立历史完整。

### 合并

- 预览包含字段、来源、关系、绑定和动作影响；
- 预览过期返回 409；
- 单事务失败时无部分合并；
- 重复关系去重；
- 旧 ID 正确重定向；
- 不产生跨项目引用。

### 迁移

- 七类旧世界观数组全部映射；
- `geography` 迁移为地点而非场景；
- 旧稳定 ID 保留，冲突 ID 有确定映射；
- 大纲揭示、章节已揭示、伏笔和时间线映射；
- 文件副本差异只报告，不覆盖数据库；
- 中途失败恢复 `legacy`；
- 切换前后数量、关键字段和引用一致；
- SQLite 和 PostgreSQL 分别运行升级、迁移、回滚演练。

### API 与性能

- cursor 前后翻页稳定，无重复或遗漏；
- 搜索旧响应不能覆盖新响应；
- 1,000 要素、10,000 关系基准；
- 权限和项目隔离；
- 归档、合并和迁移状态下的禁用行为；
- 移动草稿基础版本冲突处理。

## 11. 分阶段实现建议

### `DEV-003A`：新增模型与只读迁移

- 新表、模型、schema revision；
- 旧数据迁移器、校验器和兼容投影；
- 项目级存储模式；
- 只读列表、详情、来源和版本 API。

### `DEV-003B`：安全写入

- 创建、编辑、确认、归档；
- expected version / 409；
- 内容版本和状态事件；
- 关系 CRUD 与独立历史。

### `DEV-003C`：合并与切换

- 合并 preview/commit；
- 重定向；
- 按项目迁移切换和回滚演练；
- 1,000 要素性能验证。

### `BUG-001`

- 项目嵌套路由；
- `/project/:id/lore`；
- 移动核心导航；
- 不删除旧 `WorldviewEditor`，迁移完成前保留兼容入口。

## 12. 设计复核及采纳

网站设计者给出 `CHANGES_REQUESTED`，已全部采纳：

- 内容版本扩展为名称、摘要、类型、payload 和类型 revision 的完整快照；
- 类型定义增加稳定字段 key 和 revision；
- 关系和章节绑定建立独立历史，界面分开展示；
- 增加“地点”类型，避免旧 `geography` 与叙事“场景”混淆；
- cursor 分页不承诺页码跳转；
- 合并预览过期保留用户字段选择；
- 移动草稿按项目、设定和基础版本隔离，并定义过期和退出清理；
- 迁移期间单一事实源写入，旧 JSON 只作为派生兼容投影。

## 13. 风险与待批准事项

### 已知风险

- 自定义类型无法完全无损投影回旧七类 JSON，生产回滚前必须导出并校验；
- SQLite 大项目迁移需要分批准备和短事务切换；
- 合并会影响多个引用表，必须通过真实 PostgreSQL 集成测试；
- `normalized_name` 只用于搜索和候选提示，不能用于自动合并；
- M2、M3 可能扩展绑定证据和生成引用，但不应改变稳定 ID。

### 建议最高管理者决策

建议批准：

1. 新增内置地点类型；
2. 内容、关系和绑定独立版本化；
3. M1 归档和合并替代永久删除；
4. 自定义字段已有实例后禁止直接改类型；
5. M1 关系图为增强项；
6. M1 章节出现只展示摘要，完整编辑进入 M2；
7. 按 `DEV-003A/B/C` 分段实现和验收。

## 14. 验收自检

- [x] 覆盖全部产品类型、自定义类型及兼容地点类型；
- [x] 稳定 ID、来源、关系、版本、章节绑定和伏笔动作边界明确；
- [x] 计划与实际、候选与确认、内容与生命周期分离；
- [x] 旧 JSON 迁移可校验、可切换、可回滚；
- [x] 不修改或删除历史迁移和旧数据；
- [x] SQLite 与 PostgreSQL 兼容策略明确；
- [x] 200 章、1,000 要素的分页、索引和验证方案明确；
- [x] 设计复核的四项阻断问题已处理；
- [x] 没有引入新的付费服务；
- [x] 没有删除、移动或清空任何核心代码。
