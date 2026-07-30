# BUG-002 历史 Text/JSON 类型治理设计

## 基本信息

- 任务编号：`BUG-002`
- 文档版本：v1.0
- 日期：2026-07-30
- 当前状态：`APPROVED`
- 负责人：核心开发者
- 协作角色：最高管理者、网站设计者
- 当前阶段：只读预检与迁移设计
- 明确限制：本阶段不连接或修改真实数据库，不执行 Alembic 迁移，不转换、删除或覆盖任何用户数据

## 1. 背景与目标

现有 ORM 将世界观、章节大纲、章节状态和故事记忆中的结构化字段声明为
`JSON`，但初始 Alembic 迁移 `f65673b6a290` 在 PostgreSQL 中将其中 16
个字段创建为 `Text`。SQLite 的 JSON 实现本身使用文本亲和类型，因此本地
SQLite 测试没有暴露该差异；PostgreSQL 会按 Text 返回字符串，导致运行时
获得的类型与 ORM 预期不一致。

`DEV-003A` 已在只读 Lore 投影层兼容历史 Text JSON，但该兼容不是物理模型
治理的替代品。本任务目标是形成一套可验证、可回滚、不会猜测修改小说内容的
治理方案，供后续真实迁移专项审批。

## 2. 设计结论

1. 不修改任何历史迁移；使用新的 Alembic revision 纠正已有环境。
2. PostgreSQL 目标类型采用 `JSON`，不采用 `JSONB`。
3. SQLite 分支明确 no-op，不重建表。
4. 真实转换前必须完成只读预检，并要求所有阻断异常为零或逐条获得处置批准。
5. SQL `NULL` 原样保留；不自动变为 `[]` 或 `{}`。
6. 空字符串、非法 JSON、错误顶层类型、JSON literal `null` 和双重编码默认阻断。
7. 不得把异常内容静默清空、截断、猜测修复或写入普通日志。
8. 真实转换必须在受影响写入冻结、备份恢复演练通过和项目负责人再次批准后执行。
9. 本次设计批准不等于真实数据转换批准。

## 3. 证据与字段盘点

### 3.1 受影响字段

| 表 | 字段 | ORM 类型 | 初始 PostgreSQL 类型 | 合法顶层形态 |
|---|---|---|---|---|
| `worldviews` | `characters` | JSON | Text | array |
| `worldviews` | `geography` | JSON | Text | array |
| `worldviews` | `factions` | JSON | Text | array |
| `worldviews` | `power_system` | JSON | Text | array |
| `worldviews` | `history` | JSON | Text | array |
| `worldviews` | `conflicts` | JSON | Text | array |
| `worldviews` | `special_settings` | JSON | Text | array |
| `worldviews` | `parsed_elements` | JSON | Text | array；已知旧结构可为 object |
| `outlines` | `reveal_plan` | JSON | Text | array |
| `outlines` | `chapters` | JSON | Text | array |
| `chapters` | `revealed_elements` | JSON | Text | array |
| `story_memories` | `revealed_elements` | JSON | Text | array |
| `story_memories` | `character_states` | JSON | Text | object |
| `story_memories` | `foreshadows` | JSON | Text | array |
| `story_memories` | `timeline` | JSON | Text | array |
| `story_memories` | `chapter_summaries` | JSON | Text | array |

共计 4 张表、16 个字段。

### 3.2 不属于本任务

- `community_novels.liked_by`：ORM 与迁移均为 JSON。
- `DEV-003A` 新表中的 `field_schema`、`payload`、`locator`、`counts` 和
  `validation_errors`：ORM 与迁移均为 JSON。
- `raw_text`、`story_arc`、章节正文、摘要和社区文本：设计上就是 Text。
- JSON 内容结构升级、字段重命名、内容清洗或业务语义修复。

### 3.3 代码证据

- ORM：`backend/app/models/project.py`
- 初始迁移：
  `backend/alembic/versions/2026_07_13_0855-f65673b6a290_initial_schema.py`
- 当前 Lore 兼容读取：`backend/app/core/lore_migration.py`
- PostgreSQL 16.4 基线：`docker-compose.yml`、`.github/workflows/ci.yml`

## 4. 只读预检设计

### 4.1 预检原则

- 只能执行只读查询。
- 报告默认只包含表、字段、分类计数、受影响行 ID 和内容哈希。
- 不把小说正文、设定内容、数据库连接信息或原始异常值写入 CI 或普通日志。
- 预检需要在常规运行期执行一次，并在写入冻结后紧邻迁移再执行一次。
- 两次结果不一致时停止迁移。

### 4.2 分类

每个受影响单元必须归入且只归入以下一类：

| 分类 | 默认处理 |
|---|---|
| SQL `NULL` | 合法并原样保留 |
| 合法空 array `[]` | 合法 |
| 合法空 object `{}` | 仅 `character_states` 合法 |
| `parsed_elements` object | 已知历史兼容；单独计数并验证旧解析器 |
| 合法且顶层形态正确 | 合法 |
| JSON literal `null` | 阻断，等待语义决定 |
| 空字符串或纯空白 | 阻断 |
| 非法 JSON | 阻断 |
| string/number/boolean 顶层 | 阻断 |
| 双重编码的 array/object | 阻断并人工确认 |
| array 字段中的 object | 阻断，`parsed_elements` 例外 |
| object 字段中的 array | 阻断 |

### 4.3 聚合预检 SQL 形态

正式实现应使用 PostgreSQL 16 的 `IS JSON` 谓词，将 16 个字段展开为统一
结果集，再按表、字段和预期类型聚合：

```sql
WITH cells(table_name, row_id, column_name, expected_type, value) AS (
    SELECT 'worldviews', id, 'characters', 'array', characters FROM worldviews
    UNION ALL SELECT 'worldviews', id, 'geography', 'array', geography FROM worldviews
    UNION ALL SELECT 'worldviews', id, 'factions', 'array', factions FROM worldviews
    UNION ALL SELECT 'worldviews', id, 'power_system', 'array', power_system FROM worldviews
    UNION ALL SELECT 'worldviews', id, 'history', 'array', history FROM worldviews
    UNION ALL SELECT 'worldviews', id, 'conflicts', 'array', conflicts FROM worldviews
    UNION ALL SELECT 'worldviews', id, 'special_settings', 'array', special_settings FROM worldviews
    UNION ALL SELECT 'worldviews', id, 'parsed_elements', 'array_or_object', parsed_elements FROM worldviews
    UNION ALL SELECT 'outlines', id, 'reveal_plan', 'array', reveal_plan FROM outlines
    UNION ALL SELECT 'outlines', id, 'chapters', 'array', chapters FROM outlines
    UNION ALL SELECT 'chapters', id, 'revealed_elements', 'array', revealed_elements FROM chapters
    UNION ALL SELECT 'story_memories', id, 'revealed_elements', 'array', revealed_elements FROM story_memories
    UNION ALL SELECT 'story_memories', id, 'character_states', 'object', character_states FROM story_memories
    UNION ALL SELECT 'story_memories', id, 'foreshadows', 'array', foreshadows FROM story_memories
    UNION ALL SELECT 'story_memories', id, 'timeline', 'array', timeline FROM story_memories
    UNION ALL SELECT 'story_memories', id, 'chapter_summaries', 'array', chapter_summaries FROM story_memories
),
classified AS (
    SELECT
        *,
        CASE WHEN value IS JSON THEN value::json END AS parsed
    FROM cells
)
SELECT
    table_name,
    column_name,
    expected_type,
    count(*) AS total_rows,
    count(*) FILTER (WHERE value IS NULL) AS sql_null,
    count(*) FILTER (WHERE value IS NOT NULL AND btrim(value) = '') AS blank_text,
    count(*) FILTER (
        WHERE value IS NOT NULL
          AND btrim(value) <> ''
          AND NOT (value IS JSON)
    ) AS invalid_json,
    count(*) FILTER (WHERE json_typeof(parsed) = 'null') AS json_null,
    count(*) FILTER (WHERE json_typeof(parsed) = 'array') AS json_array,
    count(*) FILTER (WHERE json_typeof(parsed) = 'object') AS json_object,
    count(*) FILTER (
        WHERE parsed IS NOT NULL
          AND json_typeof(parsed) NOT IN ('array', 'object', 'null')
    ) AS json_scalar
FROM classified
GROUP BY table_name, column_name, expected_type
ORDER BY table_name, column_name;
```

实现时还必须单独检测双重编码 JSON、当前物理列类型、表行数、表大小、长事务、
阻塞锁、剩余磁盘空间和复制延迟。任何查询不得输出原始小说内容。

### 4.4 预检门禁

满足以下条件才能申请真实执行：

- 16 个字段与实际数据库 schema 完全一致。
- 空白、非法 JSON、JSON literal `null`、错误顶层类型和双重编码均为 0；
  或每一条都有精确、审计化且经批准的处置方案。
- `parsed_elements` object 数量已确认，旧解析结果通过抽样和自动验证。
- 数据库没有阻塞迁移的长事务。
- 表大小、WAL、磁盘和预计锁时长已有等量副本测量结果。

## 5. 目标类型选择

首次纠正采用 PostgreSQL `JSON`：

- 与 SQLAlchemy 通用 `JSON` 声明一致。
- 保留比 JSONB 更多的原始文本特征，包括键顺序和重复键语义。
- 当前功能不依赖 JSONB 索引或运算。
- 避免无依据引入 PostgreSQL 专属 ORM 类型。

不选择 JSONB 的原因：

- JSONB 会规范化内容并折叠重复键。
- 转换后的字节形态不能从数据库类型回滚恢复。
- 表重写、WAL 和存储风险更高。
- 当前没有查询性能证据支持该变化。

JSONB 只能作为未来独立性能任务重新论证。

## 6. 迁移实现方案

### 6.1 新 revision

- 新建 Alembic revision，接在当前 head 之后。
- 禁止编辑 `f65673b6a290` 或其他已有 revision。
- PostgreSQL 分支执行 Text → JSON。
- SQLite 分支明确 no-op。
- 其他未支持方言明确失败，不能静默跳过。

### 6.2 小型 Beta 数据库推荐路径

在预检为零异常且等量演练证明锁时间可接受时，对每张表一次性转换全部相关字段：

```sql
ALTER TABLE worldviews
    ALTER COLUMN characters TYPE json USING characters::json,
    ALTER COLUMN geography TYPE json USING geography::json,
    ALTER COLUMN factions TYPE json USING factions::json,
    ALTER COLUMN power_system TYPE json USING power_system::json,
    ALTER COLUMN history TYPE json USING history::json,
    ALTER COLUMN conflicts TYPE json USING conflicts::json,
    ALTER COLUMN special_settings TYPE json USING special_settings::json,
    ALTER COLUMN parsed_elements TYPE json USING parsed_elements::json;
```

`outlines`、`chapters` 和 `story_memories` 采用同样方式。

执行要求：

1. 冻结 worldview、outline、chapter 和 story memory 的所有写入。
2. 已打开表单保留本地草稿并允许复制，不能静默丢失。
3. 获取部署级事务 advisory lock（实现优先使用
   `pg_try_advisory_xact_lock`），防止两个迁移实例并行，并避免会话异常后遗留锁。
4. 设置较短 `lock_timeout`；取锁失败立即安全终止。
5. 写入冻结后再次预检。
6. 在一个数据库事务内转换四张表，任何失败整体回滚。
7. 转换后验证物理类型、行数、NULL 数量、形态计数和语义 checksum。
8. API 冒烟与一致性检查通过前保持只读。
9. 验证通过后才恢复写入。

### 6.3 大表替代路径

如果等量演练显示直接转换的锁时间、WAL 或复制延迟不可接受，禁止执行上述
直接方案，另行设计：

1. 新增影子 JSON 列。
2. 分批回填。
3. 双读比较。
4. 冻结写入。
5. 短锁交换列名。
6. 保留旧 Text 列到单独清理批准点。

影子列路径属于新的架构和工期决定，不在本设计中自动启用。

## 7. 备份与恢复

真实执行前必须同时具备：

- 托管数据库快照或完整 custom-format `pg_dump`。
- 4 张受影响表的独立逻辑备份。
- 备份加密、最小访问权限和明确保留期限。
- 数据库版本、Alembic revision、行数、表大小和备份校验记录。
- 在隔离数据库完成实际恢复演练。

项目 JSON 文件只能作为辅助证据，不能代替数据库备份。

回滚分三类：

1. 迁移事务提交前失败：数据库事务整体回滚，列保持 Text。
2. 迁移提交后、恢复写入前失败：执行 JSON → Text downgrade，并完成 API 验证。
3. 恢复写入后发现问题：重新冻结写入、生成最新备份并专项评估；禁止直接 downgrade。

JSON → Text 能恢复逻辑内容，但不能承诺字节级一致；字节级恢复依赖预迁移备份。

## 8. 维护与用户体验

### 8.1 推荐维护流程

1. 计划维护：告知影响范围、时间窗口和草稿处理方式。
2. 停止新写入：相关保存 API 返回统一、可识别的维护状态。
3. 准备资料：备份与第二次预检。
4. 检查完整性：异常为零才能继续。
5. 升级存储：保持只读。
6. 复核结果：API、数量和一致性验证。
7. 恢复编辑或执行已验证回滚。

默认使用阶段式状态，不伪造百分比。

### 8.2 用户文案边界

推荐对用户描述为“正在升级旧项目资料的存储格式”。不得展示：

- SQL、Text/JSON、表名和字段名；
- 堆栈、驱动错误或数据库连接信息；
- 原始异常内容；
- 未经验证的“已恢复”或“数据绝对安全”承诺。

异常时只显示受影响项目和可识别区域、查看/编辑影响、保护措施、下一次更新时间
和事件编号。技术详情进入权限受控的管理报告。

### 8.3 状态与可访问性

- 维护、只读、失败和恢复状态必须同时使用文字、图标和颜色。
- 不确定进度使用 `role="status"`，阶段变化使用节制的
  `aria-live="polite"`。
- 失败、回滚失败和数据异常使用阻断警报。
- 禁用保存操作旁显示可读原因，不能只降低透明度。
- 支持桌面、390px、键盘、读屏和 200% 缩放。
- 普通文字对比度至少 4.5:1，焦点和状态图形至少 3:1。
- 成功通知保留到用户确认已读；人工处理提示保留到问题关闭。

### 8.4 草稿保护生命周期

- 保存操作遇到维护响应时，不清空或覆盖当前表单；立即提供“复制草稿”和
  “稍后重试”，并说明服务端尚未保存。
- 草稿按当前用户、项目和编辑对象隔离，其他用户或项目不得读取。
- 页面刷新、返回项目和维护结束后重新进入时，都应恢复最近一份未保存草稿；
  恢复范围和保留期限必须在界面中说明。
- 草稿只在成功保存、用户明确放弃，或保留期结束且用户确认后清除；维护结束
  不能自动清除。
- 维护结束后先比较服务端版本。没有冲突时继续保存；出现版本冲突时保留本地
  草稿并提供对比、复制和重新应用入口，不能静默覆盖。

### 8.5 结果反馈定义

| 状态 | 必须说明 | 主操作 | 持续时间 |
|---|---|---|---|
| 失败 | 当前仍为只读、草稿状态、事件编号和下一次更新时间 | 复制草稿、返回项目 | 保留到恢复或转人工处理 |
| 回滚中 | 正在恢复旧存储、当前不可保存、草稿仍保留 | 复制草稿、查看状态 | 保留到回滚完成或失败 |
| 回滚成功 | 已恢复编辑能力、未保存草稿如何继续、验证时间 | 恢复草稿、继续编辑 | 保留到用户确认已读 |
| 升级成功 | 已恢复编辑能力、草稿和服务端版本检查结果 | 继续编辑 | 保留到用户确认已读 |
| 人工处理 | 受影响项目/区域、只读影响、保护措施、事件编号和下次更新 | 复制草稿、返回项目 | 保留到事件关闭 |

迁移结果写入项目级审计状态；维护期间离线的用户下次进入受影响项目时仍能看到
最终结果。所有状态均不得显示 SQL、表字段、堆栈、连接信息或原始小说内容。

### 8.6 响应式与可访问性验收

- 390px 宽度无横向溢出；维护原因、草稿操作和返回入口在不横向滚动时可见。
- 键盘可到达状态提示、复制、重试和返回操作，并有清晰焦点。
- 进入阻断状态时，焦点落到状态标题；后续阶段更新不得反复抢夺焦点。
- 读屏器能区分普通阶段更新与阻断错误，且不会重复播报未变化内容。
- 200% 缩放时不丢失内容、草稿操作或返回入口。
- 尊重 `prefers-reduced-motion`，减少或关闭非必要动画。

## 9. 当前实现依赖

仓库目前没有统一维护模式或项目级写入冻结机制。真实迁移实现前必须增加：

- 部署级或应用级维护开关；
- 对 worldview、outline、chapter 和 story memory 全部写入口的一致拦截；
- 已打开表单的草稿保留或复制能力；
- 管理端迁移状态与审计记录；
- 成功、失败、回滚中、回滚成功和人工处理五类独立状态。

如果无法证明受影响读取稳定，应使用全平台维护页；否则推荐只冻结受影响写入。

## 10. 测试与演练矩阵

### PostgreSQL 16.4

- 16 个字段从旧 Text schema 升级。
- SQL `NULL`、空 array、空 object、Unicode、深层对象。
- 空字符串、空白、非法 JSON、JSON literal `null`。
- string/number/boolean 顶层类型和双重编码。
- `parsed_elements` array 与已知 object 结构。
- 非法数据使迁移在任何 DDL 前失败。
- upgrade → downgrade → upgrade。
- 失败注入证明不存在部分表转换。
- 转换前后行数、NULL 数量、形态计数和逐行语义 checksum 一致。
- worldview、outline、chapter、memory 和 Lore API 全量回归。
- 项目删除和级联关系回归。
- 其他事务持锁时按 `lock_timeout` 失败。
- 等量副本记录锁时长、WAL、磁盘和复制延迟。

### SQLite

- migration 明确 no-op。
- upgrade → downgrade → upgrade 不改变表结构或数据。
- 后端全量测试通过。
- 不使用 batch rebuild。

### 恢复

- 完整备份恢复到隔离数据库。
- 受影响表独立恢复。
- 事务内失败自动回滚。
- 提交后、恢复写入前 downgrade。
- UI 和 API 正确区分失败、回滚中、回滚成功和人工处理。

## 11. 分段实施建议

- `BUG-002A`：实现只读预检命令、聚合报告和 PostgreSQL/SQLite 测试。
- `BUG-002B`：实现维护开关、写入冻结、用户状态和审计。
- `BUG-002C`：实现新 Alembic revision、备份/恢复演练和等量数据压测。
- `BUG-002D`：项目负责人针对精确数据库、窗口、备份和回滚记录批准真实执行。

每一阶段独立验收；`BUG-002A/B/C` 通过不自动授权 `BUG-002D`。

## 12. 设计验收标准

- 16 个字段盘点完整，且不包含本来就是 Text 的业务字段。
- 初始迁移保持不变。
- PostgreSQL 目标为 JSON，SQLite 明确 no-op。
- 预检不会写数据库或输出原始内容。
- 非法与含义不明确的数据默认阻断。
- SQL `NULL`、JSON null、空集合和双重编码策略明确。
- 备份已要求实际恢复演练，而非只检查文件存在。
- 转换具有锁超时、单事务和终止条件。
- 恢复写入前完成物理类型、数量、checksum 和 API 验证。
- 维护状态、草稿、失败、回滚和人工处理体验完整。
- 桌面、390px、键盘、读屏和 200% 缩放验收点明确。
- 真实数据转换仍有独立专项批准点。

## 13. 剩余决策与推荐

1. 维护范围：推荐只冻结受影响写入；读取不稳定时升级为全平台维护。
2. JSON null：推荐阻断，不自动等同 SQL `NULL`。
3. `parsed_elements` object：推荐允许已知旧结构，但必须单独计数和验证。
4. 超时策略：推荐优先执行已验证回滚；回滚无法确认时继续只读。
5. 通知：推荐维护前 24 小时、1 小时和开始时提供站内通知；是否发邮件另行决定。
6. 大表阈值：由等量副本演练得出，不在没有数据时猜测固定行数。
7. 实际执行：只有完整门禁证据形成后，再向项目负责人申请精确批准。

## 14. 当前结论

`BUG-002` 迁移设计已通过核心开发者与网站设计者复核，并由最高管理者验收为
`APPROVED`。本文件没有授权或执行真实数据转换，也没有删除、移动或覆盖任何
核心代码、迁移或用户数据。
