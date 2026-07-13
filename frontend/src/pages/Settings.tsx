import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/services/api";

interface Provider {
  name: string;
  base_url: string;
  models: string[];
}

export default function Settings() {
  const navigate = useNavigate();
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("https://api.openai.com/v1");
  const [model, setModel] = useState("gpt-4o");
  const [temperature, setTemperature] = useState(0.8);
  const [maxTokens, setMaxTokens] = useState(4096);
  const [isConfigured, setIsConfigured] = useState(false);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [selectedProvider, setSelectedProvider] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    loadSettings();
    loadProviders();
  }, []);

  async function loadSettings() {
    try {
      const s = await api.getLLMSettings();
      setApiKey(s.api_key);
      setBaseUrl(s.base_url);
      setModel(s.model);
      setTemperature(s.temperature);
      setMaxTokens(s.max_tokens);
      setIsConfigured(s.is_configured);
    } catch (e) {
      console.error("Failed to load settings:", e);
    } finally {
      setLoaded(true);
    }
  }

  async function loadProviders() {
    try {
      const data = await api.getProviders();
      setProviders(data.providers);
    } catch (e) {
      console.error("Failed to load providers:", e);
    }
  }

  function handleProviderChange(name: string) {
    setSelectedProvider(name);
    const provider = providers.find((p) => p.name === name);
    if (provider) {
      if (provider.base_url) setBaseUrl(provider.base_url);
      if (provider.models.length > 0) setModel(provider.models[0]);
    }
  }

  async function handleSave() {
    setSaving(true);
    try {
      const s = await api.saveLLMSettings({
        api_key: apiKey,
        base_url: baseUrl,
        model,
        temperature,
        max_tokens: maxTokens,
      });
      setApiKey(s.api_key);
      setIsConfigured(s.is_configured);
      alert("设置已保存");
    } catch (e) {
      alert("保存失败: " + (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    try {
      await api.saveLLMSettings({
        api_key: apiKey,
        base_url: baseUrl,
        model,
        temperature,
        max_tokens: maxTokens,
      });
      const result = await api.testLLMConnection();
      if (result.success) {
        setTestResult({ success: true, message: `连接成功！模型回复: ${result.reply}` });
      } else {
        setTestResult({ success: false, message: result.error || "连接失败" });
      }
    } catch (e) {
      setTestResult({ success: false, message: (e as Error).message });
    } finally {
      setTesting(false);
    }
  }

  if (!loaded) return <div className="empty-state">加载中...</div>;

  return (
    <div style={{ maxWidth: "680px" }}>
      <button className="btn-back" onClick={() => navigate(-1)}>← 返回</button>

      <div className="page-header">
        <h1>API 设置</h1>
        <p>配置 AI 大模型 API Key，用于生成小说内容</p>
      </div>

      {/* Status card */}
      <div className="card" style={{
        background: isConfigured ? "var(--gold-light)" : "var(--red-light)",
        borderColor: isConfigured ? "var(--gold)" : "var(--red)",
        borderLeftWidth: "4px",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <span style={{
            display: "inline-block",
            width: "8px",
            height: "8px",
            borderRadius: "50%",
            background: isConfigured ? "var(--gold)" : "var(--red)",
          }} />
          <span style={{ fontWeight: 600, fontSize: "14px", color: isConfigured ? "var(--gold-dark)" : "var(--red)" }}>
            {isConfigured ? "API 已配置 — AI 生成功能可用" : "API 未配置 — 当前使用开发模拟模式"}
          </span>
        </div>
      </div>

      {/* Provider presets */}
      <div className="card">
        <label className="form-label">选择服务商（快速填充）</label>
        <select className="form-select" value={selectedProvider} onChange={(e) => handleProviderChange(e.target.value)}>
          <option value="">— 选择服务商或手动填写 —</option>
          {providers.map((p) => (
            <option key={p.name} value={p.name}>{p.name}</option>
          ))}
        </select>
        {selectedProvider && (
          <div style={{ marginTop: "0.625rem", display: "flex", gap: "0.375rem", flexWrap: "wrap" }}>
            {providers.find((p) => p.name === selectedProvider)?.models.map((m) => (
              <button
                key={m}
                className="btn btn-sm"
                onClick={() => setModel(m)}
                style={{
                  borderColor: model === m ? "var(--gold)" : "var(--border)",
                  background: model === m ? "var(--gold-light)" : "var(--white)",
                  color: model === m ? "var(--gold-dark)" : "var(--text-2)",
                }}
              >
                {m}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* API Key and configuration */}
      <div className="card">
        <div className="form-group">
          <label className="form-label">API Key</label>
          <input
            className="form-input"
            type="password"
            placeholder="sk-..."
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
          <p className="form-hint">在服务商平台申请的 API Key。已保存的 Key 会以星号显示，修改时直接输入新 Key 即可。</p>
        </div>

        <div className="form-group">
          <label className="form-label">API Base URL</label>
          <input
            className="form-input"
            type="text"
            placeholder="https://api.openai.com/v1"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
          <p className="form-hint">OpenAI 兼容接口地址。不同服务商地址不同，可通过上方快速选择。</p>
        </div>

        <div className="form-group">
          <label className="form-label">模型名称</label>
          <input
            className="form-input"
            type="text"
            placeholder="gpt-4o"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem" }}>
          <div className="form-group">
            <label className="form-label">温度 (Temperature)</label>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <input
                type="range"
                min="0"
                max="2"
                step="0.1"
                value={temperature}
                onChange={(e) => setTemperature(Number(e.target.value))}
                style={{ flex: 1, accentColor: "var(--gold)" }}
              />
              <span style={{ fontSize: "13px", minWidth: "30px", textAlign: "right", fontWeight: 600, color: "var(--gold-dark)" }}>{temperature}</span>
            </div>
            <p className="form-hint">0 = 保守确定，2 = 天马行空</p>
          </div>
          <div className="form-group">
            <label className="form-label">最大 Token 数</label>
            <select className="form-select" value={maxTokens} onChange={(e) => setMaxTokens(Number(e.target.value))}>
              <option value={2048}>2048 · 短</option>
              <option value={4096}>4096 · 标准</option>
              <option value={8192}>8192 · 长</option>
              <option value={16384}>16384 · 超长</option>
            </select>
          </div>
        </div>

        {testResult && (
          <div style={{
            marginTop: "0.75rem",
            padding: "0.625rem 0.875rem",
            borderRadius: "var(--r-md)",
            background: testResult.success ? "var(--gold-light)" : "var(--red-light)",
            border: `1px solid ${testResult.success ? "var(--gold-border)" : "#f5c6ce"}`,
            borderLeftWidth: "3px",
            borderLeftColor: testResult.success ? "var(--gold)" : "var(--red)",
            fontSize: "13px",
            color: testResult.success ? "var(--gold-dark)" : "var(--red)",
          }}>
            {testResult.success ? "✓ " : "✗ "}{testResult.message}
          </div>
        )}

        <div style={{ display: "flex", gap: "0.5rem", marginTop: "1rem" }}>
          <button className="btn btn-primary btn-lg" onClick={handleSave} disabled={saving}>
            {saving ? "保存中..." : "保存设置"}
          </button>
          <button className="btn btn-lg" onClick={handleTest} disabled={testing || !apiKey.trim()}>
            {testing ? "测试中..." : "测试连接"}
          </button>
        </div>
      </div>

      {/* Help card */}
      <div className="card" style={{ background: "var(--bg)" }}>
        <div className="wv-section-title">使用说明</div>
        <ul style={{ fontSize: "13px", color: "var(--text-2)", paddingLeft: "1.25rem", lineHeight: 2 }}>
          <li>本系统支持任何 OpenAI 兼容的 API 接口</li>
          <li>在对应服务商平台注册并获取 API Key</li>
          <li>选择服务商可自动填充 Base URL 和模型名称</li>
          <li>保存后点击「测试连接」验证配置是否正确</li>
          <li>配置成功后，大纲生成、章节写作、世界观导入都将使用真实 AI</li>
          <li>API Key 保存在本地服务器，不会上传到任何第三方</li>
        </ul>
      </div>
    </div>
  );
}
