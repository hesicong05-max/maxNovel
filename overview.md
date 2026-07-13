# 社区功能模块 — 实施总结

## 功能概述

为小说世界观续写 Agent 添加完整的社区功能：用户可上传小说到社区展示，自定义标签分类，主页支持无限下滑随机加载其他用户作品，专属编辑界面编写详细描述信息，并可开放「共创世界观」权限。

## 新增后端 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/community/novels` | GET | 分页列表，支持 tag 筛选 + latest/popular/random 排序 |
| `/api/community/novels/random` | GET | 随机加载（下拉刷新），支持 exclude 排除已加载 ID |
| `/api/community/novels` | POST | 上传小说到社区（可关联项目自动填充统计） |
| `/api/community/novels/{id}` | GET | 小说详情（自动增加阅读数） |
| `/api/community/novels/{id}` | PUT | 编辑小说（简介/梗概/章节说明/标签/共创开关） |
| `/api/community/novels/{id}` | DELETE | 从社区移除小说 |
| `/api/community/novels/{id}/like` | POST | 点赞 |
| `/api/community/tags` | GET | 标签列表（按使用量排序） |
| `/api/community/projects/{id}/stats` | GET | 获取项目章节数和字数（上传时用） |

## 数据模型

- **CommunityNovel**: title, author_name, genre, project_id, synopsis, story_outline, chapter_notes, allow_cocreation, view_count, like_count, total_chapters, total_words, tags(多对多)
- **CommunityTag**: name(唯一), usage_count, novels(多对多)
- **novel_tag_association**: novel_id + tag_id 关联表

## 前端新增页面

1. **Community.tsx** — 社区主页
   - IntersectionObserver 实现无限下滑加载
   - 标签筛选 + 最新/热门/随机排序
   - 随机刷新按钮重新加载内容
   - 小说卡片网格展示（标题/作者/类型/标签/简介/阅读赞/章节数/共创标识）

2. **CommunityNovelDetail.tsx** — 小说详情页
   - 完整展示简介/故事梗概/章节说明
   - 标签列表 + 阅读数/点赞/章节/总字数统计
   - 点赞按钮 + 基于世界观共创入口
   - 编辑/删除操作

3. **CommunityEdit.tsx** — 上传/编辑界面
   - 关联项目选择（自动填充标题/类型/章节/字数统计）
   - 动态标签增删（Enter/逗号添加，Backspace删除，点击移除，最多10个）
   - 小说简介、故事梗概、章节说明三个独立文本区域（含字数计数）
   - 「是否允许共创世界观」开关（带视觉反馈）
   - 前端参数校验（标题必填/长度限制/简介长度限制）

## 关键技术决策

- 字段存储在独立表中（CommunityNovel），不污染原有 Project 表
- 标签使用多对多关联表，支持标签复用和使用计数
- 无限滚动使用 IntersectionObserver + 已加载 ID 排除机制，避免重复加载
- async SQLAlchemy 关系懒加载问题通过 `db.refresh(novel, ["tags"])` 预加载解决

## 验证结果
- 后端模块导入通过
- 前端 TypeScript 零错误
- 6 个社区 API 端点全部注册
- 端到端测试通过：创建→列表→详情→点赞→标签→更新→随机加载→删除

## 测试地址
- 前端：http://localhost:5173
- 后端 API 文档：http://localhost:8000/docs
