import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/services/api";
import type { LoreFieldDefinition, LoreMergeOperation } from "@/types/lore";

function message(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  return error instanceof Error ? error.message : "无法加载合并历史。";
}

function actionLabel(action: string): string {
  if (action === "rewired") return "关系已改连至保留项";
  if (action === "exact_duplicate_archived") return "重复关系已停止生效并保留审计记录";
  return "合并后形成的自身关系已停止生效";
}

function choiceLabel(choice: unknown): string {
  if (choice === "survivor") return "采用保留项";
  if (choice === "merged") return "采用被合并项";
  if (choice === "manual") return "采用手动填写";
  return "未记录";
}

function summary(operation: LoreMergeOperation): { survivorName: string; mergedName: string; sources: number; deletions: number } {
  const impact = operation.impact_summary ?? {};
  const names = impact.element_names && typeof impact.element_names === "object"
    ? impact.element_names as Record<string, unknown>
    : {};
  const sourceImpact = impact.source_impact && typeof impact.source_impact === "object"
    ? impact.source_impact as Record<string, unknown>
    : {};
  return {
    survivorName: typeof names.survivor === "string" ? names.survivor : "保留项",
    mergedName: typeof names.merged === "string" ? names.merged : "另一项设定",
    sources: typeof sourceImpact.preserved_total === "number" ? sourceImpact.preserved_total : 0,
    deletions: typeof impact.physical_deletions === "number" ? impact.physical_deletions : 0,
  };
}

export default function LoreMergeHistory({ projectId, elementId, fieldDefinitions = [], refreshToken = 0 }: {
  projectId: string;
  elementId: string;
  fieldDefinitions?: LoreFieldDefinition[];
  refreshToken?: number;
}) {
  const [items, setItems] = useState<LoreMergeOperation[]>([]);
  const [opened, setOpened] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadToken, setReloadToken] = useState(0);
  const sequence = useRef(0);

  useEffect(() => {
    if (!opened) return;
    const controller = new AbortController();
    const current = ++sequence.current;
    setLoading(true);
    setError("");
    api.listLoreElementMergeHistory(projectId, elementId, controller.signal)
      .then((response) => {
        if (current !== sequence.current) return;
        setItems(Array.from(new Map(response.items.map((item) => [item.id, item])).values()));
      })
      .catch((nextError) => {
        if ((nextError as Error).name !== "AbortError" && current === sequence.current) setError(message(nextError));
      })
      .finally(() => {
        if (current === sequence.current) setLoading(false);
      });
    return () => controller.abort();
  }, [elementId, opened, projectId, refreshToken, reloadToken]);

  return <section className="lore-merge-history" aria-busy={opened && loading}>
    <h3>合并历史</h3>
    <p className="lore-note">这里是不可变审计记录，不是撤销入口。</p>
    {!opened && <button className="btn btn-secondary" type="button" onClick={() => setOpened(true)}>查看合并历史</button>}
    {opened && loading && <div className="lore-empty">合并历史加载中…</div>}
    {error && <div className="lore-alert" role="alert">{error}<button type="button" onClick={() => setReloadToken((value) => value + 1)}>重试</button></div>}
    {opened && !loading && !error && items.length === 0 && <div className="lore-empty"><strong>尚无合并记录</strong><span>这项设定还没有作为保留项或被合并项参与正式合并。</span></div>}
    {opened && items.map((operation) => {
      const info = summary(operation);
      const role = operation.survivor_element_id === elementId ? "保留项" : "被合并项";
      return <article key={operation.id}>
        <header><strong>{info.mergedName} → {info.survivorName}</strong><span>{new Date(operation.created_at).toLocaleString()} · 当前设定为{role}</span></header>
        <dl><div><dt>保留项版本</dt><dd>{operation.survivor_before_content_version} → {operation.survivor_after_content_version}</dd></div><div><dt>来源保留</dt><dd>{info.sources} 条</dd></div><div><dt>物理删除</dt><dd>{info.deletions === 0 ? "没有" : `${info.deletions} 项`}</dd></div></dl>
        <details><summary>查看内容选择</summary><ul><li>名称：{choiceLabel(operation.selection_snapshot.name)}</li><li>摘要：{choiceLabel(operation.selection_snapshot.summary)}</li>{fieldDefinitions.map((field) => {
          const choices = operation.selection_snapshot.fields && typeof operation.selection_snapshot.fields === "object"
            ? operation.selection_snapshot.fields as Record<string, unknown>
            : {};
          return <li key={field.key}>{field.label}：{choiceLabel(choices[field.key])}</li>;
        })}</ul></details>
        {operation.relation_actions.length === 0 ? <p>没有需要处理的关系。</p> : <details><summary>查看 {operation.relation_actions.length} 条关系处理</summary><ul>{operation.relation_actions.map((action) => <li key={action.id}>{actionLabel(action.action)}</li>)}</ul></details>}
      </article>;
    })}
  </section>;
}
