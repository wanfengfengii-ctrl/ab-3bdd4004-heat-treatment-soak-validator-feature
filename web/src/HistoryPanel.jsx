// 最近记录列表面板：与上传/结论区域清晰分离。
// 自身只有 loading / error / empty / list 四种展示，查询失败不波及当前结论；
// 后台刷新失败时保留旧列表，仅额外显示错误提示。

export default function HistoryPanel({ state, onSelect }) {
  const showLoading = state.status === "loading" && state.items.length === 0;
  const showEmpty =
    state.status !== "loading" && state.items.length === 0 && !state.message;

  return (
    <section className="history" aria-labelledby="history-title">
      <h2 id="history-title">最近记录</h2>

      {showLoading && (
        <p className="history-hint" data-testid="history-loading">
          正在读取最近记录…
        </p>
      )}

      {state.message && (
        <div
          role="alert"
          className="banner error history-error"
          data-testid="history-error"
        >
          {state.message}
        </div>
      )}

      {showEmpty && (
        <p className="history-hint" data-testid="history-empty">
          暂无历史记录，完成一次分析后会按时间倒序出现在这里。
        </p>
      )}

      {state.items.length > 0 && (
        <ul className="history-list">
          {state.items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                className="history-item"
                data-testid="history-item"
                data-id={item.id}
                onClick={() => onSelect(item)}
              >
                <span
                  className={`badge ${item.qualified ? "ok" : "ng"}`}
                  data-testid="history-status"
                >
                  {item.statusText}
                </span>
                <span className="history-main">
                  <span className="history-name">{item.title}</span>
                  <span className="history-meta">
                    {item.filename}
                    {item.recordCountText ? ` · ${item.recordCountText}` : ""}
                  </span>
                </span>
                <time className="history-time">{item.analyzedAtText}</time>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
