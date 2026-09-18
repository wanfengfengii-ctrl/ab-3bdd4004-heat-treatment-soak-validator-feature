// 纯函数：把 API 分析结果映射为页面上的“唯一结论”视图模型，
// 以及上传前的本地预检。判定本身由后端完成，这里只做呈现决策。

export const MAX_FILE_BYTES = 2 * 1024 * 1024; // 与后端 2 MiB 上限一致

/**
 * 解析 API 响应文本，把超出安全整数范围的整数还原为 BigInt。
 * JSON.parse 会把 > 2^53 的整数舍入（例如相差 1800 秒的两个超大时间戳
 * 被舍入成同一个数），必须借助 reviver 的 source 原文恢复精确值；
 * 不支持 source 的旧环境静默降级为原行为。
 */
export function parseJsonPreserveBigInts(text) {
  return JSON.parse(text, (key, value, context) => {
    if (
      typeof value === "number" &&
      !Number.isSafeInteger(value) &&
      typeof context?.source === "string" &&
      /^-?\d+$/.test(context.source)
    ) {
      return BigInt(context.source);
    }
    return value;
  });
}

/** 上传前预检；返回 null 表示可以提交，否则返回 { code, message }。 */
export function validateFile(file) {
  if (!file) {
    return { code: "no_file", message: "请先选择要上传的 JSON 文件" };
  }
  if (file.size === 0) {
    return { code: "empty_file", message: "文件为空，请重新选择" };
  }
  if (file.size > MAX_FILE_BYTES) {
    return {
      code: "file_too_large",
      message: `文件大小 ${file.size} 字节超过上限 ${MAX_FILE_BYTES} 字节 (2 MiB)`,
    };
  }
  return null;
}

// JS Date 只能表示 epoch ±8.64e15 毫秒，超出后 toISOString 会抛
// RangeError；合法的大整数时间戳必须安全降级，绝不能拖垮渲染。
const pad2 = (n) => String(n).padStart(2, "0");

/**
 * 秒级时间戳 → "2026-09-14 08:30:00 UTC"；
 * 超出 JS Date 可表示范围时返回 null（由调用方降级为原始秒数）。
 */
export function formatTimestamp(t) {
  if (typeof t === "bigint") return null; // 大整数必然超出 Date 可表示范围
  const ms = t * 1000;
  if (!Number.isFinite(ms)) return null;
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) return null;
  const year = String(date.getUTCFullYear()).padStart(4, "0");
  return (
    `${year}-${pad2(date.getUTCMonth() + 1)}-${pad2(date.getUTCDate())} ` +
    `${pad2(date.getUTCHours())}:${pad2(date.getUTCMinutes())}:${pad2(date.getUTCSeconds())} UTC`
  );
}

/** 可表示则给 UTC 文本，否则退回原始秒数；任何输入都不抛异常。 */
export function formatTimestampOrRaw(t) {
  return formatTimestamp(t) ?? `t = ${t}`;
}

/** 时长（秒）→ "1800 秒（30 分钟）"；BigInt 时长按整除精确显示。 */
export function formatDuration(seconds) {
  if (typeof seconds === "bigint") {
    return seconds % 60n === 0n
      ? `${seconds} 秒（${seconds / 60n} 分钟）`
      : `${seconds} 秒（约 ${(Number(seconds) / 60).toFixed(1)} 分钟）`;
  }
  const minutes = seconds / 60;
  const minuteText = Number.isInteger(minutes)
    ? `${minutes} 分钟`
    : `约 ${minutes.toFixed(1)} 分钟`;
  return `${seconds} 秒（${minuteText}）`;
}

/** 时间行：可表示时附原始秒数，超范围时只显示原始秒数。 */
function timeRow(label, t) {
  const formatted = formatTimestamp(t);
  if (formatted === null) {
    return { label, value: `t = ${t}` };
  }
  return { label, value: formatted, raw: t };
}

/**
 * 由 API 分析结果生成唯一结论：
 * - 合格：给出最早达标段的起止时间；
 * - 不合格：给出最长有效段时长（无任何有效段时长为 0）。
 */
export function buildVerdict(analysis) {
  if (analysis.qualified && analysis.earliestQualifyingSegment) {
    const seg = analysis.earliestQualifyingSegment;
    return {
      status: "qualified",
      headline: "保温合格",
      rows: [
        timeRow("达标段开始", seg.startT),
        timeRow("达标段结束", seg.endT),
        { label: "达标段时长", value: formatDuration(seg.duration) },
      ],
    };
  }
  const longest = analysis.longestSegment;
  const duration = longest ? longest.duration : 0;
  const rows = [{ label: "最长有效段时长", value: formatDuration(duration) }];
  if (longest) {
    rows.push({
      label: "最长有效段区间",
      value: `${formatTimestampOrRaw(longest.startT)} 至 ${formatTimestampOrRaw(longest.endT)}`,
    });
  }
  return { status: "unqualified", headline: "保温不合格", rows };
}
