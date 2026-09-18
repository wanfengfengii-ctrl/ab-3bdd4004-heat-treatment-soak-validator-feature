import { useEffect, useRef, useState } from "react";

import HistoryPanel from "./HistoryPanel";
import { mapHistoryItems } from "./lib/history";
import { buildVerdict, parseJsonPreserveBigInts, validateFile } from "./lib/verdict";

// 与后端 storage.RECENT_LIMIT 对应；列表顺序以后端分析时间倒序为准
const HISTORY_FETCH_FAILED = "最近记录读取失败，当前分析结论不受影响，可稍后刷新重试。";

async function parseJsonOrNull(text) {
  try {
    return text ? parseJsonPreserveBigInts(text) : null;
  } catch {
    return null;
  }
}

export default function App() {
  const [file, setFile] = useState(null);
  const [heatNo, setHeatNo] = useState("");
  const [verdict, setVerdict] = useState(null); // 唯一结论
  const [verdictSource, setVerdictSource] = useState(null); // 结论来源（历史回看时标注）
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [history, setHistory] = useState({ status: "loading", items: [], message: null });
  const inputRef = useRef(null);

  // 页面启动时读取最近记录；查询失败只在列表区域提示，不影响上传与当前结论
  async function loadHistory() {
    // 后台刷新保留已有列表，不闪空态；首次加载（无数据）才显示加载提示
    setHistory((prev) => ({ status: "loading", items: prev.items, message: null }));
    try {
      const resp = await fetch("/api/history");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const body = await parseJsonOrNull(await resp.text());
      setHistory({ status: "ready", items: mapHistoryItems(body?.items), message: null });
    } catch {
      // 刷新失败：保留已有列表，只在列表区域提示；首次加载失败时列表为空
      setHistory((prev) => ({ status: "error", items: prev.items, message: HISTORY_FETCH_FAILED }));
    }
  }

  useEffect(() => {
    loadHistory();
  }, []);

  async function handleSubmit(event) {
    event.preventDefault();
    // 每次提交都先清除旧结果与旧错误，避免误读过期结论
    setVerdict(null);
    setVerdictSource(null);
    setError(null);

    const problem = validateFile(file);
    if (problem) {
      setError(problem.message);
      return;
    }

    setLoading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      // 炉次号可选：空串时后端按“未传/未填”处理，旧客户端行为不变
      if (heatNo.trim()) form.append("heat_no", heatNo.trim());
      const resp = await fetch("/api/analyze", { method: "POST", body: form });
      // 先取文本再自行解析：保留超出 JS 安全整数范围的时间戳（BigInt），
      // 避免 resp.json() 精度丢失把相差 1800 秒的起止舍入成同一时刻
      const body = await parseJsonOrNull(await resp.text());
      if (!resp.ok) {
        // 整份拒绝或落库失败：仅显示错误，旧结果已清除，不展示未落库结论
        const message = body?.detail?.message ?? `服务器拒绝（HTTP ${resp.status}）`;
        setError(message);
        return;
      }
      setVerdict(buildVerdict(body));
      // 成功落库后刷新最近记录列表
      loadHistory();
    } catch {
      setError("无法连接分析服务，请稍后重试");
    } finally {
      setLoading(false);
    }
  }

  // 点击一条最近记录：恢复当时的唯一结论；失败只在列表区域提示并保留当前结果
  async function handleRestore(item) {
    setHistory((prev) => ({ ...prev, message: null }));
    try {
      const resp = await fetch(`/api/history/${item.id}`);
      const body = await parseJsonOrNull(await resp.text());
      if (!resp.ok || !body?.conclusion) throw new Error("restore failed");
      setVerdict(buildVerdict(body.conclusion));
      setVerdictSource({
        heatNo: item.heatNo,
        filename: item.filename,
        analyzedAtText: item.analyzedAtText,
      });
      setError(null);
    } catch {
      setHistory((prev) => ({
        ...prev,
        message: "该记录恢复失败，可能已被清理；当前结果保留未变。",
      }));
    }
  }

  return (
    <main className="page">
      <h1>淬火炉保温段验收</h1>
      <p className="hint">
        上传温度记录 JSON（根为数组，每项含整数秒时间戳 <code>t</code> 与摄氏温度{" "}
        <code>temp</code>）。判定标准：温度连续落在 840–860&nbsp;°C、相邻采样间隔不超过
        60 秒、段持续时长达到 1800 秒。
      </p>

      <div className="layout">
        <div className="work">
          <form onSubmit={handleSubmit} className="upload">
            <label htmlFor="record-file">温度记录文件</label>
            <input
              id="record-file"
              ref={inputRef}
              type="file"
              accept=".json,application/json"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <label htmlFor="heat-no">炉次号（可选）</label>
            <input
              id="heat-no"
              type="text"
              value={heatNo}
              maxLength={64}
              placeholder="返工复测可重复填写"
              onChange={(e) => setHeatNo(e.target.value)}
            />
            <button type="submit" disabled={loading}>
              {loading ? "分析中…" : "上传并分析"}
            </button>
          </form>

          {error && (
            <div role="alert" className="banner error" data-testid="error">
              <strong>文件被拒绝：</strong>
              {error}
            </div>
          )}

          {verdict && (
            <section
              className={`banner verdict ${verdict.status}`}
              data-testid="verdict"
              aria-live="polite"
            >
              <h2 data-testid="verdict-headline">{verdict.headline}</h2>
              {verdictSource && (
                <p className="verdict-source" data-testid="verdict-source">
                  回看历史记录
                  {verdictSource.heatNo ? `：炉次 ${verdictSource.heatNo}` : ""}
                  {`（${verdictSource.filename}，${verdictSource.analyzedAtText}）`}
                </p>
              )}
              <dl>
                {verdict.rows.map((row) => (
                  <div key={row.label} className="row">
                    <dt>{row.label}</dt>
                    <dd>
                      {row.value}
                      {row.raw !== undefined && (
                        <span className="raw">（t = {row.raw}）</span>
                      )}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          )}
        </div>

        <aside className="side">
          <HistoryPanel state={history} onSelect={handleRestore} />
        </aside>
      </div>
    </main>
  );
}
