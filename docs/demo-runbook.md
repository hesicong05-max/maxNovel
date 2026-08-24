# 最终 Demo 本地运行手册

本手册只用于非生产、无真实模型的最终演示。Demo fixture 会在环境不安全时返回 404，脚本不会关闭门禁、覆盖已有数据库或修复发生漂移的数据。

## 1. 固定版本与依赖

在周一冻结的干净工作树运行，不要从仓库根目录的旧分支启动。周二不得执行 `git pull`、切换提交、安装依赖或新增迁移。

脚本不会安装依赖。显式提供已经验证的 Python 与 Vite：

```bash
export PYTHON_BIN=/absolute/path/to/backend/.venv/bin/python
export VITE_BIN=/absolute/path/to/frontend/node_modules/.bin/vite
```

如果当前 checkout 的 `backend/data/llm_settings.json` 含有 API Key，启动会失败。不要清空、移动或输出该文件；改用全新的干净工作树。

## 2. 创建隔离环境

每次排练使用新的 run-id。已有 run-id 会被拒绝，不允许通过删除数据库“复位”。

```bash
./scripts/demo-local.sh start \
  --run-id Rehearsal-R1 \
  --backend-port 18000 \
  --frontend-port 15173

./scripts/demo-local.sh check --run-id Rehearsal-R1
./scripts/demo-local.sh check --run-id Rehearsal-R1
```

首次检查允许一次注册和一次 fixture bootstrap。随后两次检查必须显示：

- `state=ready`
- `bootstrap_posts=0`
- `no_llm=true`
- 十项 fixture 计数与固定样例一致
- generation run、paid attempt、technical execution、candidate、selection、ForeshadowFact、legacy Chapter 全部为 0

显式查看登录凭据：

```bash
./scripts/demo-local.sh credentials --run-id Rehearsal-R1
```

凭据只保存在权限为 0600 的运行目录中，不提交、不截图、不写入 PPT。需要人工登录时，必须同时提供 run-id、登录 URL、邮箱和密码。

人工登录前先运行同一 run-id 的 `status` 与 `check`，再打开脚本输出的 frontend URL。若进入登录页，只能使用该 run-id 的邮箱和密码，禁止混用 Final-A、Final-B 或 Rehearsal 凭据。

`check` 是执行第五步写操作前的就绪门禁。技术模拟、手工另存或采用之后，八张零写表中的执行、候选或采用相关表将不再全部为 0，检查按预期失败；这不表示环境损坏，不得据此删除、覆盖或“复位”数据库。

## 3. 进程管理

```bash
./scripts/demo-local.sh status --run-id Rehearsal-R1
./scripts/demo-local.sh stop --run-id Rehearsal-R1
./scripts/demo-local.sh resume --run-id Rehearsal-R1
```

`stop` 只终止脚本记录且身份匹配的进程，保留数据库和日志。`resume` 要求当前 Git SHA 与创建环境时完全一致。端口被未知进程占用时脚本会停止，不会执行 `pkill`。

运行产物位于：

```text
backend/data/demo/<run-id>/
```

启动或检查失败时查看 `backend.log`、`frontend.log`、`migration.log`。不要修改失败环境；换新 run-id 重建，或切换预先验证的 Final-B。

## 4. 周一最终环境

建立四个相互隔离的环境：

- `Rehearsal-R1`、`Rehearsal-R2`：周二排练使用。
- `Final-A`：周三主演示，周二不得触碰。
- `Final-B`：现场备用，保持未执行第五步写操作。

Final-A/Final-B 冻结时应只有固定 fixture，generation、candidate、selection、ForeshadowFact 和 legacy Chapter 计数全部为 0。

## 5. 五步演示

主演示使用 Chrome 1920×1080、100% 缩放；备用为 1280×720。390 和原生 200% 只做回归，不在现场切换缩放。

1. 项目总览：项目身份、五步导航、固定样例和零 AI 口径。
2. 设定仓库：展示“沈星”的类型、摘要、来源与关系，不编辑。
3. 章节规划：展示篇章、两章与小说/篇章/章节三级共七项分层设定绑定，不拖动。
4. 伏笔计划：展示“计划不等于正文事实”，确认 ForeshadowFact 为 0。
5. 技术模拟：显式确认一次固定模拟，查看收据、候选和审计；手工另存 v2；采用并改用；刷新验证采用状态。

步骤 1–4 和候选只读浏览不得产生 POST。第五步写入次数必须与冻结讲稿一致；不要临场重复点击。

## 6. 视觉与录制门禁

在 1920 与 1280 下逐页检查：

- 五步导航只有一个当前步骤。
- `scrollWidth <= clientWidth`，没有移动底栏。
- 所有现场按钮至少 44px，焦点环为 3px。
- 技术执行和采用确认框在视口安全区内、内部可滚动、Tab 圈闭、Escape 回焦。
- 固定模拟、未调用 AI、无模型费用、不覆盖原稿、不确认伏笔事实的说明可见。
- 项目名、章节名、设定名和候选长中文不截断、不遮挡、不撑破卡片。
- Console 没有 warning/error。

素材命名：

```text
artifacts/screenshots/01-overview-five-steps.png
artifacts/screenshots/02-lore-source-relation.png
artifacts/screenshots/03-chapter-tree-assignment.png
artifacts/screenshots/04-foreshadow-boundary-fact-zero.png
artifacts/screenshots/05-technical-confirm-zero-ai.png
artifacts/screenshots/06-technical-receipt-candidate.png
artifacts/screenshots/07-manual-v2-audit.png
artifacts/screenshots/08-selection-after-refresh.png
artifacts/recordings/demo-main-1920x1080.mp4
artifacts/recordings/demo-backup-1280x720.mp4
```

录制一份 1920 主流程和一份 1280 备用流程；Final-A、Final-B 各保存一张 ready 首页截图。截图和录屏只保存页面内容，严禁包含凭据终端、邮箱、密码或 token。

## 7. 现场降级

- fixture diverged、pending 或登录异常：停止操作，切 Final-B，不修数据库。
- 技术 POST 结果未知：只走既有 by-key 核对，不重复 POST。
- 仍无法确认：播放周一录制的本地固定模拟，并明确这是预录的无 AI 技术样例。
- 手工另存或采用失败：停在候选与审计，不伪称已经采用。
- 投影布局异常：切 Chrome 100% 的 1280×720 备用窗口或备用录屏，不临场改 CSS。

## 8. 周二代码冻结

周二只使用 Rehearsal 环境练习并制作 PPT：不修改代码或配置，不安装依赖，不迁移新 revision，不切换 SHA。只有项目负责人明确解除冻结的 Demo P0 才能恢复开发。
