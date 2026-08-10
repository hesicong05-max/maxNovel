import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "@/services/api";
import { loadDraft, type DraftScope } from "@/services/maintenanceDrafts";
import type {
  LoreMigrationPreviewClassification,
  LoreMigrationPreviewResponse,
} from "@/types/lore";
import LoreMigrationUpgrade, { isStoredMigrationDraft } from "./LoreMigrationUpgrade";
import MigrationDecisionPanel from "./MigrationDecisionPanel";

type Filter = "all" | LoreMigrationPreviewClassification;

const CONTENT_EDITABLE_REASONS = new Set(["missing_name", "parsed_name_mismatch"]);

const STATUS: Record<LoreMigrationPreviewClassification, string> = {
  mappable: "可迁移",
  review_required: "待确认",
  possible_conflict: "可能冲突",
  blocked: "阻塞",
};

const CATEGORY: Record<string, string> = {
  characters: "角色",
  geography: "地点",
  factions: "阵营",
  power_system: "能力体系",
  history: "历史事件",
  conflicts: "冲突",
  special_settings: "其他重要设定",
};

const TYPE: Record<string, string> = {
  world: "世界观",
  character: "角色",
  location: "地点",
  scene: "场景",
  faction: "阵营",
  item: "物品",
  ability_system: "能力体系",
  historical_event: "历史事件",
  conflict: "冲突",
  event: "事件",
  foreshadow: "伏笔",
  rule: "规则与限制",
  race: "种族",
  social_institution: "社会制度",
  other: "其他",
};

const SOURCE: Record<string, string> = {
  manual: "手动创建",
  imported: "文档导入",
  hybrid: "文档导入与手动补充",
};

const REASON: Record<string, string> = {
  worldview_missing: "没有可检查的旧世界观资料",
  invalid_collection: "旧资料结构无法安全读取",
  missing_name: "缺少明确名称",
  non_object_entry: "该项不是可映射的结构化对象",
  source_missing: "数据来源需要确认",
  source_unknown: "数据来源类型无法识别",
  type_confirmation_required: "模块类型需要作者确认",
  parsed_name_mismatch: "结构化名称与旧解析记录不一致",
  duplicate_legacy_id: "旧解析编号重复",
  raw_text_excerpt_unverified: "尚未建立精确原文段落定位",
  unmapped_fields: "存在不能静默映射的字段",
  duplicate_name_same_type: "同类型中存在同名资料",
  duplicate_name_cross_type: "不同类型中存在同名资料",
  existing_formal_elements: "兼容项目中已存在正式设定",
  existing_element_name_collision: "与现有正式设定名称碰撞",
  existing_legacy_map: "检测到既有旧资料映射",
  existing_migration_state: "检测到既有迁移状态",
  project_not_legacy: "当前项目不是兼容资料模式",
};

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) return "旧资料在检查期间发生变化，当前结果已失效，请重新检查。";
    if (error.status === 503) return "服务暂不可用，预检未执行，旧资料未更改。请稍后重试。";
    return `${error.detail} 旧资料未受影响。`;
  }
  return "预检未完成，旧资料未受影响。请检查网络后重试。";
}

export default function LoreMigrationPreview({
  projectId,
  userId,
  onBack,
  onUpgraded,
  onEditItem = () => {},
}: {
  projectId: string;
  userId: string;
  onBack: () => void;
  onUpgraded: () => void;
  onEditItem?: (
    category: string,
    index: number,
    itemFingerprint: string,
    sourceChecksum: string
  ) => void;
}) {
  const [report, setReport] = useState<LoreMigrationPreviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [reloadToken, setReloadToken] = useState(0);
  const migrationScope = useMemo<DraftScope>(() => ({
    userId,
    projectId,
    kind: "lore-migration",
    objectId: "legacy-to-relational-v1",
  }), [projectId, userId]);
  const [migrationStageActive, setMigrationStageActive] = useState(() => {
    const loaded = loadDraft<unknown>(migrationScope);
    if (loaded.status === "corrupt") return true;
    if (loaded.status !== "available" && loaded.status !== "expired") return false;
    return isStoredMigrationDraft(loaded.draft.payload) && loaded.draft.payload.phase !== "confirming";
  });
  const headingRef = useRef<HTMLHeadingElement | null>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  useEffect(() => {
    if (migrationStageActive) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api.getLoreMigrationPreview(projectId, controller.signal)
      .then((data) => {
        setReport(data);
        setFilter("all");
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [migrationStageActive, projectId, reloadToken]);

  const visibleItems = useMemo(() => (
    report?.items.filter((item) => filter === "all" || (item.effective_classification ?? item.classification) === filter) ?? []
  ), [report, filter]);

  const filters: Array<[Filter, string, number]> = report ? [
    ["all", "全部旧资料", report.counts.legacy_total],
    ["mappable", "可迁移", report.counts.mappable],
    ["review_required", "待确认", report.counts.review_required],
    ["possible_conflict", "可能冲突", report.counts.possible_conflict],
    ["blocked", "阻塞", report.counts.blocked],
  ] : [];

  return (
    <section className="lore-migration-preview" aria-busy={!migrationStageActive && loading} aria-labelledby="lore-migration-preview-title">
      {!migrationStageActive && <button className="btn-back" type="button" onClick={onBack}>← 返回设定仓库</button>}
      <header className="page-header">
        <div>
          <h1 id="lore-migration-preview-title" ref={headingRef} tabIndex={-1}>{migrationStageActive ? "设定仓库升级" : "旧资料迁移预检"}</h1>
          <p>{migrationStageActive ? "正在处理或核对同一次升级请求。" : "检查旧世界观资料能否安全转换为独立设定模块。"}</p>
        </div>
      </header>

      {!migrationStageActive && <div className="lore-migration-preview__notice" role="note">
        <strong>预检本身只检查数据，不会自动迁移。</strong>
        <span>系统不会替你补写、选择类型、合并资料或自动保存；只有预检通过、进入安全升级窗口并由你再次确认后，系统才会开始升级，原资料不会被删除或覆盖。</span>
      </div>}

      {!migrationStageActive && loading && <div className="lore-empty" role="status">正在检查旧资料…</div>}
      {!migrationStageActive && !loading && error && (
        <div className="lore-alert" role="alert">
          {error}
          <button type="button" onClick={() => setReloadToken((value) => value + 1)}>重新检查</button>
        </div>
      )}

      {!migrationStageActive && !loading && report && <>
        <div className={`lore-migration-preview__result is-${report.overall_status}`} role={report.overall_status === "blocked" ? "alert" : "status"}>
          <strong>{report.overall_status === "ready" ? "预检已通过" : report.overall_status === "blocked" ? "本次预检未通过" : "预检完成，仍有资料需要确认"}</strong>
          <span>共检查 {report.counts.legacy_total} 项；未创建或修改任何设定。</span>
          <small>检查时间：{new Date(report.checked_at).toLocaleString("zh-CN")}</small>
        </div>

        <section className="lore-migration-preview__summary" aria-label="预检摘要">
          {filters.map(([key, label, count]) => (
            <button key={key} type="button" aria-pressed={filter === key} onClick={() => setFilter(key)}>
              <strong>{count}</strong><span>{label}</span>
            </button>
          ))}
        </section>

        {report.issues.some((issue) => issue.legacy_category === null) && (
          <section className="lore-migration-preview__blockers" aria-label="项目级阻塞原因">
            <h2>需要先处理</h2>
            {report.issues.filter((issue) => issue.legacy_category === null).map((issue) => (
              <div key={issue.case_id} className="lore-alert" role={issue.severity === "blocked" ? "alert" : "status"}>
                <strong>{REASON[issue.reason_code] ?? issue.message}</strong>
                <span>{issue.recommended_action}</span>
              </div>
            ))}
          </section>
        )}

        <section className="lore-migration-preview__items" aria-label="旧资料逐项结果">
          <h2>逐项检查结果</h2>
          {report.counts.legacy_total === 0 ? (
            <div className="lore-empty"><strong>未找到可检查的旧世界观资料</strong><span>本次未做任何更改。</span></div>
          ) : visibleItems.length === 0 ? (
            <div className="lore-empty">当前筛选下没有旧资料。</div>
          ) : visibleItems.map((item) => (
            <details className={`lore-migration-preview__item is-${item.effective_classification ?? item.classification}`} key={item.planned_element_id}>
              <summary>
                <span><strong>{item.name || "未命名资料"}</strong><small>旧世界观 › {CATEGORY[item.legacy_category] ?? item.legacy_category} › 第 {item.legacy_index + 1} 项</small></span>
                <span className="lore-badge lore-badge--muted">{STATUS[item.effective_classification ?? item.classification]}</span>
              </summary>
              <div className="lore-migration-preview__detail">
                <dl>
                  <div><dt>迁移后模块类型</dt><dd>{(() => {
                    const typeKey = item.effective_proposed_type_key ?? item.proposed_type_key;
                    if (!typeKey) return "需要作者确认";
                    return TYPE[typeKey] ?? "未识别类型，请重新预检";
                  })()}</dd></div>
                  <div><dt>资料来源</dt><dd>{(() => {
                    const sourceKind = item.effective_source_kind ?? item.source_kind;
                    if (!sourceKind) return "待确认";
                    const label = SOURCE[sourceKind];
                    if (!label) return "未识别来源，请重新预检";
                    return item.effective_source_kind && item.effective_source_kind !== item.source_kind
                      ? `${label}（作者已确认）`
                      : label;
                  })()}</dd></div>
                  <div><dt>原文定位</dt><dd>{item.exact_excerpt_available ? "已建立精确段落定位" : "未建立原文段落定位"}</dd></div>
                </dl>
                {(item.effective_reason_codes ?? item.reason_codes).length > 0 && <div className="lore-migration-preview__reasons"><strong>仍需处理</strong><ul>{(item.effective_reason_codes ?? item.reason_codes).map((reason) => <li key={reason}>{REASON[reason] ?? reason}</li>)}</ul></div>}
                <details><summary>查看原始结构化内容</summary><pre>{JSON.stringify(item.original_value, null, 2)}</pre></details>
                <details><summary>系统计划如何整理（仅预览）</summary><pre>{JSON.stringify(item.effective_mapped_fields ?? item.mapped_fields, null, 2)}</pre></details>
                {item.reason_codes.some((reason) => CONTENT_EDITABLE_REASONS.has(reason)) && (
                  <button
                    className="btn btn-secondary"
                    type="button"
                    onClick={() => onEditItem(
                      item.legacy_category,
                      item.legacy_index,
                      item.item_fingerprint,
                      report.source_checksum
                    )}
                  >
                    去修改这项原资料
                  </button>
                )}
                <MigrationDecisionPanel
                  projectId={projectId}
                  userId={userId}
                  report={report}
                  item={item}
                  onChanged={() => setReloadToken((value) => value + 1)}
                />
              </div>
            </details>
          ))}
        </section>

      </>}

      <LoreMigrationUpgrade
        projectId={projectId}
        userId={userId}
        report={report}
        onRequestPreviewReload={() => setReloadToken((value) => value + 1)}
        onUpgraded={onUpgraded}
        onMigrationStageChange={setMigrationStageActive}
      />

      {!migrationStageActive && !loading && report && <div className="lore-migration-preview__actions">
        <button className="btn btn-secondary" type="button" onClick={() => setReloadToken((value) => value + 1)}>重新检查</button>
        <button className="btn btn-secondary" type="button" onClick={onBack}>返回设定仓库</button>
      </div>}
    </section>
  );
}
