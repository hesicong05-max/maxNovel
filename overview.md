# 章节生成模块全面审查与修复

## 审查范围
完整链路：API(chapters.py) → Prompt(templates.py) → LLM(llm_client.py) → Memory(memory_store.py) → 前端(ChapterWriter.tsx)

## 发现的问题与修复

### P0（严重）
| # | 问题 | 修复 |
|---|------|------|
| 1 | **max_tokens 截断**：`_generate_chapter_core` 调用 `chat_stream` 时未传 `max_tokens`，使用默认 4096 导致 3000 字中文章节在 ~2000 字处截断 | 根据目标字数动态计算：`min(max(effective_wc * 2 + 500, user_max_tokens, 2048), 8192)` |
| 2 | **章节缺失世界观上下文**：`build_chapter_prompt` 只传入 `elements_to_reveal`（本章要素），LLM 看不到全局故事方向和合法名称列表，容易编造与世界观不符的内容 | 新增 `story_arc` + `all_element_names` 参数；system prompt 加入"世界观即一切"原则 + 所有合法要素名称列表 |

### P1（重要）
| # | 问题 | 修复 |
|---|------|------|
| 3 | **chapter_num 无范围校验**：用户可能请求 chapter 0 或 999 | `generate_chapter` 端点校验 1~total_chapters；`_generate_chapter_core` 安全网校验 |
| 4 | **批量成功检测脆弱**：用字符串匹配 `"type": "complete"` 检测，可能因 JSON 格式变化而失效 | 改为 JSON 解析 |

### P2（改进）
| # | 问题 | 修复 |
|---|------|------|
| 5 | **_mock_chapter 硬编码**：返回固定的"林远"等内容，与用户世界观无关 | 从 prompt 提取世界观要素名称生成 mock 内容 |
| 6 | **Markdown 格式**：部分 LLM 会输出 # 标题、**加粗** 等 Markdown 格式 | prompt 新增"直接输出纯文本正文，不要使用 Markdown 格式" |

## 修改文件
- `backend/app/prompts/templates.py` — `build_chapter_prompt` 新增 2 参数 + 世界观约束
- `backend/app/api/chapters.py` — max_tokens 计算 + 校验 + JSON 解析
- `backend/app/core/llm_client.py` — `_mock_chapter` 改进
- `backend/tests/test_llm_client.py` — 更新测试

## 验证
- 448 项后端测试全部通过（+1 新增）
- tsc --noEmit 零错误
- 已推送 GitHub commit `0313fb8`
