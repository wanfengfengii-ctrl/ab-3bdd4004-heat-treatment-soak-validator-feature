// 纯函数：把“最近记录”接口的摘要映射为页面列表视图模型。
// 与上传分析流程完全解耦：历史查询失败只影响列表区域，不触碰当前结论。

/**
 * 服务端分析时间（epoch 秒）→ "2026-09-14 08:30:05 UTC"；
 * 缺失或非法时返回兜底文本，任何输入都不抛异常。
 */
export function formatAnalyzedAt(epochSeconds) {
  if (epochSeconds === null || epochSeconds === undefined) return "时间未知";
  const n = Number(epochSeconds);
  if (!Number.isFinite(n)) return "时间未知";
  const date = new Date(n * 1000);
  if (Number.isNaN(date.getTime())) return "时间未知";
  const p2 = (v) => String(v).padStart(2, "0");
  // 与结论时间戳一致，统一展示 UTC，避免工程师跨时区误读
  return (
    `${date.getUTCFullYear()}-${p2(date.getUTCMonth() + 1)}-${p2(date.getUTCDate())} ` +
    `${p2(date.getUTCHours())}:${p2(date.getUTCMinutes())}:${p2(date.getUTCSeconds())} UTC`
  );
}

/** 单条摘要 → 列表项视图模型；炉次号缺省（旧请求/未填）有明确占位。 */
export function mapHistoryItem(item) {
  const heatNo = item.heatNo == null ? null : String(item.heatNo);
  const count = item.recordCount == null ? NaN : Number(item.recordCount);
  return {
    id: item.id,
    heatNo,
    title: heatNo ? `炉次 ${heatNo}` : "未填炉次号",
    filename: item.filename || "未知文件",
    qualified: Boolean(item.qualified),
    statusText: item.qualified ? "合格" : "不合格",
    recordCountText: Number.isFinite(count) ? `${count} 条记录` : "",
    analyzedAtText: formatAnalyzedAt(item.analyzedAt),
  };
}

/** 摘要数组 → 视图模型数组，保持后端给出的分析时间倒序。 */
export function mapHistoryItems(items) {
  return Array.isArray(items) ? items.map(mapHistoryItem) : [];
}
