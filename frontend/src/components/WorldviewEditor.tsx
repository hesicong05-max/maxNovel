import { useEffect, useRef, useState } from "react";
import { api } from "@/services/api";
import type { Character, Conflict, Faction, Geography, HistoryEvent, PowerSystem, SpecialSetting, WorldviewData, WorldviewSource } from "@/types";

interface Props {
  projectId: string;
  hasWorldview: boolean;
  genre: string;
  onComplete: () => void;
  onBack: () => void;
}

type EditorMode = "manual" | "import" | "hybrid";

const EMPTY_WORLDVIEW: WorldviewData = {
  characters: [],
  geography: [],
  factions: [],
  power_system: [],
  history: [],
  conflicts: [],
  special_settings: [],
};

export default function WorldviewEditor({ projectId, hasWorldview, genre, onComplete, onBack }: Props) {
  const [mode, setMode] = useState<EditorMode>("manual");
  const [data, setData] = useState<WorldviewData>(EMPTY_WORLDVIEW);
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [parsedInfo, setParsedInfo] = useState<{ total: number; by_category: Record<string, number>; by_priority: Record<string, number> } | null>(null);
  const [importText, setImportText] = useState("");
  const [importResult, setImportResult] = useState<{ count: number; done: boolean } | null>(null);
  const [source, setSource] = useState<WorldviewSource>("manual");
  const [saved, setSaved] = useState(false);  // 本地追踪保存状态，解决 hasWorldview prop 闭锁问题
  const loadedRef = useRef(false);  // 防止已加载的数据被 hasWorldview 变化覆盖
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 始终尝试加载世界观 — 不仅依赖 hasWorldview prop（该 prop 可能因父组件未刷新而过时）
  useEffect(() => {
    loadedRef.current = false;  // projectId 变化时重置
    loadWorldview();
  }, [projectId]);

  // 如果父组件刷新后 hasWorldview 变为 true，也重新加载 — 但仅在尚未加载时
  useEffect(() => {
    if (hasWorldview && !loadedRef.current) loadWorldview();
  }, [hasWorldview]);

  async function loadWorldview() {
    try {
      const wv = await api.getWorldview(projectId);
      if (!wv) return;  // 无世界观时不报错，静默返回
      loadedRef.current = true;  // 标记已加载，防止后续 hasWorldview 变化覆盖
      setData({
        characters: wv.characters || [],
        geography: wv.geography || [],
        factions: wv.factions || [],
        power_system: wv.power_system || [],
        history: wv.history || [],
        conflicts: wv.conflicts || [],
        special_settings: wv.special_settings || [],
        raw_text: wv.raw_text,
        source: (wv.source as WorldviewSource) || "manual",
      });
      setSaved(true);  // 已有世界观数据，标记为已保存
      if (wv.source === "imported") { setMode("import"); setSource("imported"); }
      else if (wv.source === "hybrid") { setMode("hybrid"); setSource("hybrid"); }
      if (wv.parsed_elements) {
        const byCat: Record<string, number> = {};
        const byPri: Record<string, number> = {};
        for (const e of wv.parsed_elements) {
          byCat[e.category] = (byCat[e.category] || 0) + 1;
          byPri[e.priority] = (byPri[e.priority] || 0) + 1;
        }
        setParsedInfo({ total: wv.parsed_elements.length, by_category: byCat, by_priority: byPri });
      }
    } catch (e) {
      // 404 表示无世界观，属正常情况
      if (e instanceof Error && !e.message.includes("404")) {
        console.error("Failed to load worldview:", e);
      }
    }
  }

  function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setImporting(true);
    // All file types go through the backend for robust encoding detection
    api.uploadWorldviewFile(projectId, file)
      .then((result) => setImportText(result.text))
      .catch((err) => alert("文件解析失败: " + (err as Error).message))
      .finally(() => { setImporting(false); if (fileInputRef.current) fileInputRef.current.value = ""; });
  }

  async function handleImport() {
    if (!importText.trim() || importText.trim().length < 10) { alert("请输入或上传至少 10 个字符的文档内容"); return; }
    setImporting(true);
    setImportResult(null);
    try {
      const result = await api.importWorldview(projectId, importText, genre);
      setData({
        characters: result.characters || [],
        geography: result.geography || [],
        factions: result.factions || [],
        power_system: result.power_system || [],
        history: result.history || [],
        conflicts: result.conflicts || [],
        special_settings: result.special_settings || [],
        raw_text: importText,
        source: mode === "hybrid" ? "hybrid" : "imported",
      });
      setSource(mode === "hybrid" ? "hybrid" : "imported");
      setImportResult({ count: result.element_count, done: true });
    } catch (e) {
      alert("导入解析失败: " + (e as Error).message);
    } finally {
      setImporting(false);
    }
  }

  async function handleSave() {
    setLoading(true);
    try {
      await api.setWorldview(projectId, { ...data, source });
      setSaved(true);  // 立即标记为已保存，使"进入下一步"按钮显示
      await loadWorldview();
      alert("世界观已保存");
    } catch (e) {
      alert("保存失败: " + (e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  function switchMode(newMode: EditorMode) {
    setMode(newMode);
    if (newMode === "manual") setSource("manual");
    else if (newMode === "import" && !importResult?.done) setSource("imported");
    else if (newMode === "hybrid") setSource("hybrid");
  }

  const isReadOnly = mode === "import" && importResult?.done;
  const showEditor = mode === "manual" || importResult?.done === true;

  // CRUD helpers
  function addCharacter() { setData({ ...data, characters: [...data.characters, { name: "", personality: "", background: "", motivation: "", ability: "", relations: [] }] }); }
  function updateCharacter(i: number, field: keyof Character, value: string) { const c = [...data.characters]; c[i] = { ...c[i], [field]: value }; setData({ ...data, characters: c }); }
  function removeCharacter(i: number) { setData({ ...data, characters: data.characters.filter((_, idx) => idx !== i) }); }
  function addGeography() { setData({ ...data, geography: [...data.geography, { name: "", description: "", significance: "" }] }); }
  function updateGeography(i: number, field: keyof Geography, value: string) { const g = [...data.geography]; g[i] = { ...g[i], [field]: value }; setData({ ...data, geography: g }); }
  function removeGeography(i: number) { setData({ ...data, geography: data.geography.filter((_, idx) => idx !== i) }); }
  function addFaction() { setData({ ...data, factions: [...data.factions, { name: "", stance: "", power_level: "", relations: [] }] }); }
  function updateFaction(i: number, field: keyof Faction, value: string) { const f = [...data.factions]; f[i] = { ...f[i], [field]: value }; setData({ ...data, factions: f }); }
  function removeFaction(i: number) { setData({ ...data, factions: data.factions.filter((_, idx) => idx !== i) }); }
  function addPowerSystem() { setData({ ...data, power_system: [...data.power_system, { name: "", levels: "", rules: "", limitations: "" }] }); }
  function updatePowerSystem(i: number, field: keyof PowerSystem, value: string) { const p = [...data.power_system]; p[i] = { ...p[i], [field]: value }; setData({ ...data, power_system: p }); }
  function removePowerSystem(i: number) { setData({ ...data, power_system: data.power_system.filter((_, idx) => idx !== i) }); }
  function addHistory() { setData({ ...data, history: [...data.history, { event: "", time: "", description: "", impact: "" }] }); }
  function updateHistory(i: number, field: keyof HistoryEvent, value: string) { const h = [...data.history]; h[i] = { ...h[i], [field]: value }; setData({ ...data, history: h }); }
  function removeHistory(i: number) { setData({ ...data, history: data.history.filter((_, idx) => idx !== i) }); }
  function addConflict() { setData({ ...data, conflicts: [...data.conflicts, { name: "", type: "", parties: "", stakes: "", resolution_hint: "" }] }); }
  function updateConflict(i: number, field: keyof Conflict, value: string) { const c = [...data.conflicts]; c[i] = { ...c[i], [field]: value }; setData({ ...data, conflicts: c }); }
  function removeConflict(i: number) { setData({ ...data, conflicts: data.conflicts.filter((_, idx) => idx !== i) }); }
  function addSpecial() { setData({ ...data, special_settings: [...data.special_settings, { name: "", description: "", rules: "" }] }); }
  function updateSpecial(i: number, field: keyof SpecialSetting, value: string) { const s = [...data.special_settings]; s[i] = { ...s[i], [field]: value }; setData({ ...data, special_settings: s }); }
  function removeSpecial(i: number) { setData({ ...data, special_settings: data.special_settings.filter((_, idx) => idx !== i) }); }

  const MODES = [
    { key: "manual" as const, label: "手动创建", desc: "逐项填写世界观模板" },
    { key: "import" as const, label: "导入文档", desc: "上传/粘贴文本，AI 自动提取" },
    { key: "hybrid" as const, label: "混合模式", desc: "导入后可手动追加修改" },
  ];

  return (
    <div>
      <button className="btn-back" onClick={onBack}>← 返回项目详情</button>

      {/* Mode selector */}
      <div className="card">
        <div className="wv-section-title">世界观创建方式</div>
        <div style={{ display: "flex", gap: "0.625rem", flexWrap: "wrap" }}>
          {MODES.map((m) => (
            <button
              key={m.key}
              className="btn"
              onClick={() => switchMode(m.key)}
              style={{
                flex: "1 1 180px",
                flexDirection: "column",
                alignItems: "flex-start",
                padding: "0.625rem 0.875rem",
                borderColor: mode === m.key ? "var(--gold)" : "var(--border)",
                background: mode === m.key ? "var(--gold-light)" : "var(--white)",
                borderWidth: "2px",
              }}
            >
              <span style={{ fontWeight: 600, fontSize: "14px", color: mode === m.key ? "var(--gold-dark)" : "var(--text-1)" }}>{m.label}</span>
              <span style={{ fontSize: "11px", color: "var(--text-3)", marginTop: "2px" }}>{m.desc}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Import panel */}
      {mode !== "manual" && (
        <div className="card">
          <div className="wv-section-title">导入世界观文档</div>
          <p className="form-hint" style={{ marginBottom: "0.625rem" }}>
            粘贴或上传包含世界观设定的文档（支持 .txt / .md / .doc / .docx），AI 将自动提取角色、地理、势力、体系等结构化要素。
          </p>
          <textarea
            className="form-textarea"
            style={{ minHeight: "140px", fontFamily: "var(--font-mono)", fontSize: "12px" }}
            placeholder={"在此粘贴世界观文档内容...\n\n例如：\n林远，男，18岁，性格坚韧...\n苍澜大陆分为东南西北四大区域...\n修炼境界：聚气境、筑基境、金丹境..."}
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
            disabled={importing}
          />
          <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.625rem", alignItems: "center" }}>
            <input ref={fileInputRef} type="file" accept=".txt,.md,.markdown,.doc,.docx" onChange={handleFileUpload} style={{ display: "none" }} />
            <button className="btn" onClick={() => fileInputRef.current?.click()} disabled={importing}>
              {importing ? "解析文件中..." : "上传文件"}
            </button>
            <button className="btn btn-primary" onClick={handleImport} disabled={importing || !importText.trim()}>
              {importing ? "AI 解析中..." : "开始导入解析"}
            </button>
            {importResult?.done && (
              <span className="tag tag-gold" style={{ fontWeight: 600 }}>已提取 {importResult.count} 个要素</span>
            )}
          </div>
          {mode === "hybrid" && importResult?.done && (
            <div style={{ marginTop: "0.625rem", padding: "0.5rem 0.75rem", background: "var(--gold-light)", borderRadius: "var(--r-md)", fontSize: "12px", color: "var(--gold-dark)", borderLeft: "3px solid var(--gold)" }}>
              混合模式已激活 — 下方表单已填充提取的要素，你可以自由编辑、添加或删除任意内容
            </div>
          )}
          {importResult?.done && !saved && (
            <div style={{ marginTop: "0.625rem", padding: "0.5rem 0.75rem", background: "#fef9e7", borderRadius: "var(--r-md)", fontSize: "13px", color: "#7d6608", borderLeft: "3px solid #f39c12" }}>
              ⚠️ 世界观已提取但尚未保存。请点击下方「保存世界观」按钮保存后，再进入下一步生成大纲。
            </div>
          )}
        </div>
      )}

      {/* Worldview editor */}
      {showEditor && (
        <>
          {/* Characters */}
          <div className="card">
            <div className="wv-section-title">角色 ({data.characters.length})</div>
            {data.characters.map((c, i) => (
              <div key={i} className="wv-entry">
                <input className="form-input" placeholder="姓名" value={c.name} onChange={(e) => updateCharacter(i, "name", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="性格" value={c.personality} onChange={(e) => updateCharacter(i, "personality", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="背景" value={c.background} onChange={(e) => updateCharacter(i, "background", e.target.value)} readOnly={isReadOnly} />
                <div style={{ display: "flex", gap: "0.375rem" }}>
                  <input className="form-input" placeholder="动机" value={c.motivation} onChange={(e) => updateCharacter(i, "motivation", e.target.value)} readOnly={isReadOnly} />
                  {!isReadOnly && <button className="btn btn-ghost btn-sm" onClick={() => removeCharacter(i)} style={{ color: "var(--red)" }}>✕</button>}
                </div>
                <input className="form-input" placeholder="能力/特长" value={c.ability} onChange={(e) => updateCharacter(i, "ability", e.target.value)} readOnly={isReadOnly} style={{ gridColumn: "1 / -1" }} />
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addCharacter}>+ 添加角色</button>}
          </div>

          {/* Geography */}
          <div className="card">
            <div className="wv-section-title">地理设定 ({data.geography.length})</div>
            {data.geography.map((g, i) => (
              <div key={i} className="wv-entry">
                <input className="form-input" placeholder="地名" value={g.name} onChange={(e) => updateGeography(i, "name", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="描述" value={g.description} onChange={(e) => updateGeography(i, "description", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="重要性" value={g.significance} onChange={(e) => updateGeography(i, "significance", e.target.value)} readOnly={isReadOnly} />
                {!isReadOnly && <button className="btn btn-ghost btn-sm" onClick={() => removeGeography(i)} style={{ color: "var(--red)" }}>✕</button>}
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addGeography}>+ 添加地点</button>}
          </div>

          {/* Factions */}
          <div className="card">
            <div className="wv-section-title">势力组织 ({data.factions.length})</div>
            {data.factions.map((f, i) => (
              <div key={i} className="wv-entry">
                <input className="form-input" placeholder="势力名称" value={f.name} onChange={(e) => updateFaction(i, "name", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="立场" value={f.stance} onChange={(e) => updateFaction(i, "stance", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="实力等级" value={f.power_level} onChange={(e) => updateFaction(i, "power_level", e.target.value)} readOnly={isReadOnly} />
                {!isReadOnly && <button className="btn btn-ghost btn-sm" onClick={() => removeFaction(i)} style={{ color: "var(--red)" }}>✕</button>}
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addFaction}>+ 添加势力</button>}
          </div>

          {/* Power System */}
          <div className="card">
            <div className="wv-section-title">力量体系 ({data.power_system.length})</div>
            {data.power_system.map((ps, i) => (
              <div key={i} className="wv-entry">
                <input className="form-input" placeholder="体系名称" value={ps.name} onChange={(e) => updatePowerSystem(i, "name", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="等级划分" value={ps.levels} onChange={(e) => updatePowerSystem(i, "levels", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="规则" value={ps.rules} onChange={(e) => updatePowerSystem(i, "rules", e.target.value)} readOnly={isReadOnly} />
                <div style={{ display: "flex", gap: "0.375rem" }}>
                  <input className="form-input" placeholder="限制" value={ps.limitations} onChange={(e) => updatePowerSystem(i, "limitations", e.target.value)} readOnly={isReadOnly} />
                  {!isReadOnly && <button className="btn btn-ghost btn-sm" onClick={() => removePowerSystem(i)} style={{ color: "var(--red)" }}>✕</button>}
                </div>
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addPowerSystem}>+ 添加体系</button>}
          </div>

          {/* History */}
          <div className="card">
            <div className="wv-section-title">历史事件 ({data.history.length})</div>
            {data.history.map((h, i) => (
              <div key={i} className="wv-entry">
                <input className="form-input" placeholder="事件名称" value={h.event} onChange={(e) => updateHistory(i, "event", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="时间" value={h.time} onChange={(e) => updateHistory(i, "time", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="描述" value={h.description} onChange={(e) => updateHistory(i, "description", e.target.value)} readOnly={isReadOnly} />
                <div style={{ display: "flex", gap: "0.375rem" }}>
                  <input className="form-input" placeholder="影响" value={h.impact} onChange={(e) => updateHistory(i, "impact", e.target.value)} readOnly={isReadOnly} />
                  {!isReadOnly && <button className="btn btn-ghost btn-sm" onClick={() => removeHistory(i)} style={{ color: "var(--red)" }}>✕</button>}
                </div>
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addHistory}>+ 添加事件</button>}
          </div>

          {/* Conflicts */}
          <div className="card">
            <div className="wv-section-title">核心矛盾 ({data.conflicts.length})</div>
            {data.conflicts.map((c, i) => (
              <div key={i} className="wv-entry">
                <input className="form-input" placeholder="矛盾名称" value={c.name} onChange={(e) => updateConflict(i, "name", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="类型" value={c.type} onChange={(e) => updateConflict(i, "type", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="涉及方" value={c.parties} onChange={(e) => updateConflict(i, "parties", e.target.value)} readOnly={isReadOnly} />
                <div style={{ display: "flex", gap: "0.375rem" }}>
                  <input className="form-input" placeholder="利害关系" value={c.stakes} onChange={(e) => updateConflict(i, "stakes", e.target.value)} readOnly={isReadOnly} />
                  {!isReadOnly && <button className="btn btn-ghost btn-sm" onClick={() => removeConflict(i)} style={{ color: "var(--red)" }}>✕</button>}
                </div>
                <input className="form-input" placeholder="解决线索" value={c.resolution_hint} onChange={(e) => updateConflict(i, "resolution_hint", e.target.value)} readOnly={isReadOnly} style={{ gridColumn: "1 / -1" }} />
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addConflict}>+ 添加矛盾</button>}
          </div>

          {/* Special Settings */}
          <div className="card">
            <div className="wv-section-title">特殊设定 ({data.special_settings.length})</div>
            {data.special_settings.map((ss, i) => (
              <div key={i} className="wv-entry">
                <input className="form-input" placeholder="设定名称" value={ss.name} onChange={(e) => updateSpecial(i, "name", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="描述" value={ss.description} onChange={(e) => updateSpecial(i, "description", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="规则" value={ss.rules} onChange={(e) => updateSpecial(i, "rules", e.target.value)} readOnly={isReadOnly} />
                {!isReadOnly && <button className="btn btn-ghost btn-sm" onClick={() => removeSpecial(i)} style={{ color: "var(--red)" }}>✕</button>}
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addSpecial}>+ 添加设定</button>}
          </div>

          {/* Parsed elements info */}
          {parsedInfo && parsedInfo.total > 0 && (
            <div className="card" style={{ background: "var(--gold-light)", borderColor: "var(--gold-border)", borderLeftWidth: "3px", borderLeftColor: "var(--gold)" }}>
              <div style={{ fontWeight: 600, marginBottom: "0.5rem", color: "var(--gold-dark)", fontSize: "14px" }}>
                世界观已解析 — 共 {parsedInfo.total} 个要素
              </div>
              <div style={{ display: "flex", gap: "0.375rem", flexWrap: "wrap" }}>
                {Object.entries(parsedInfo.by_priority).map(([pri, count]) => (
                  <span key={pri} className={`tag tag-${pri}`}>
                    {pri === "core" ? "核心" : pri === "important" ? "重要" : pri === "secondary" ? "次要" : "背景"}: {count}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Actions — 底部导航按钮 */}
          <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.875rem", flexWrap: "wrap" }}>
            <button className="btn btn-primary btn-lg" onClick={handleSave} disabled={loading}>
              {loading ? "保存中..." : "保存世界观"}
            </button>
            {/* saved 或 hasWorldview 任一为 true 即显示"进入下一步" — 解决 prop 闭锁问题 */}
            {(hasWorldview || saved) && (
              <button className="btn btn-danger btn-lg" onClick={onComplete}>
                进入下一步 →
              </button>
            )}
          </div>
        </>
      )}

      {mode === "manual" && !hasWorldview && (
        <div className="card" style={{ background: "var(--gold-light)", borderColor: "var(--gold-border)", borderLeftWidth: "3px", borderLeftColor: "var(--gold)" }}>
          <p style={{ fontSize: "13px", color: "var(--gold-dark)", lineHeight: 1.7 }}>
            填写世界观架构。系统会自动解析为结构化要素并分配优先级（核心/重要/次要/背景），
            然后根据渐进式揭示策略安排各章节的信息展开节奏。
          </p>
        </div>
      )}
    </div>
  );
}
