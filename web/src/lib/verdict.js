// 纯函数：把 API 分析结果映射为页面上的“唯一结论”视图模型，
// 以及上传前的本地预检。判定本身由后端完成，这里只做呈现决策。

export const MODE_STRICT = "strict";
export const MODE_LINEAR = "linear_equivalent";

export const MAX_FILE_BYTES = 2 * 1024 * 1024; // 与后端 2 MiB 上限一致

/** 无模式字段的旧记录/旧响应一律按严格判定读取。 */
export function resolveAnalysisMode(analysis) {
  return analysis?.analysisMode === MODE_LINEAR ? MODE_LINEAR : MODE_STRICT;
}

/** 判定方式的页面文案（历史摘要与结论共用）。 */
export function modeLabel(mode) {
  return mode === MODE_LINEAR ? "线性曲线等效保温" : "严格判定";
}

export function modeShortLabel(mode) {
  return mode === MODE_LINEAR ? "等效保温" : "严格判定";
}

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
  // 非整数秒（如线性段物理时长）保留至多 3 位小数
  const shown = Number.isInteger(seconds)
    ? seconds
    : Math.round(seconds * 1000) / 1000;
  const minutes = shown / 60;
  const minuteText = Number.isInteger(minutes)
    ? `${minutes} 分钟`
    : `约 ${minutes.toFixed(1)} 分钟`;
  return `${shown} 秒（${minuteText}）`;
}

/** 十进制秒偏移：保留至多 3 位小数并去掉尾零（10.000 → "10"，15.429 保留）。 */
export function formatOffset(offset) {
  const n = Number(offset);
  if (!Number.isFinite(n)) return "0";
  return String(Math.round(n * 1000) / 1000);
}

/** 等效秒计数：至多 3 位小数（1800 → "1800"，43.280851 → "43.281"）。 */
export function formatSeconds(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return String(Math.round(n * 1000) / 1000);
}

/** 等效秒 → "1800 等效秒（30 分钟）"。 */
export function formatEquivalent(value) {
  const n = Number(value);
  const minutes = Number.isFinite(n) ? n / 60 : NaN;
  const minuteText = Number.isInteger(minutes)
    ? `${minutes} 分钟`
    : `约 ${minutes.toFixed(1)} 分钟`;
  return `${formatSeconds(value)} 等效秒（${minuteText}）`;
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
 * 线性模式的时间行：整数锚点 + 十进制秒偏移。
 * 锚点可 BigInt（超大时间戳）；偏移非零时文本追加 “ + 15.429 秒”，
 * 且不再另附原始秒数以免抹掉偏移。
 */
function anchorTimeRow(label, anchor, offset) {
  const suffix = Number(offset) !== 0 ? ` + ${formatOffset(offset)} 秒` : "";
  const formatted = typeof anchor === "bigint" ? null : formatTimestamp(anchor);
  if (formatted === null) {
    return { label, value: `t = ${anchor}${suffix}` };
  }
  return suffix
    ? { label, value: `${formatted}${suffix}` }
    : { label, value: formatted, raw: anchor };
}

function anchorRangeText(seg) {
  // 用于“区间”行：把起止两个锚点+偏移各自展开为文本
  const start = anchorTimeRow("", seg.startAnchorT, seg.startOffset ?? 0).value;
  const end = anchorTimeRow("", seg.endAnchorT, seg.endOffset ?? 0).value;
  return `${start} 至 ${end}`;
}

/**
 * 由 API 分析结果生成唯一结论：
 * - 合格：给出最早达标段的起止时间；
 * - 不合格：给出最长有效段时长（无任何有效段时长为 0）。
 * verdict.mode 标明记录所用判定方式（无模式字段的旧记录按 strict）。
 */
export function buildVerdict(analysis) {
  const mode = resolveAnalysisMode(analysis);
  if (mode === MODE_LINEAR) {
    return buildLinearVerdict(analysis);
  }
  return buildStrictVerdict(analysis);
}

function buildStrictVerdict(analysis) {
  if (analysis.qualified && analysis.earliestQualifyingSegment) {
    const seg = analysis.earliestQualifyingSegment;
    return {
      mode: MODE_STRICT,
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
  return { mode: MODE_STRICT, status: "unqualified", headline: "保温不合格", rows };
}

function buildLinearVerdict(analysis) {
  if (analysis.qualified && analysis.earliestQualifyingSegment) {
    const seg = analysis.earliestQualifyingSegment;
    const rows = [
      anchorTimeRow("达标段开始", seg.startAnchorT, seg.startOffset ?? 0),
      anchorTimeRow("首次达标时刻", seg.endAnchorT, seg.endOffset ?? 0),
      { label: "累计等效秒", value: formatEquivalent(seg.equivalentSeconds) },
    ];
    if (typeof seg.physicalSeconds === "number") {
      rows.push({
        label: "段内物理时长",
        value: formatDuration(seg.physicalSeconds),
      });
    }
    return {
      mode: MODE_LINEAR,
      status: "qualified",
      headline: "保温合格",
      rows,
    };
  }
  const longest = analysis.longestSegment;
  const rows = [
    {
      label: "最长连续段等效秒",
      value: formatEquivalent(longest ? longest.equivalentSeconds : 0),
    },
  ];
  if (longest) {
    rows.push({ label: "最长连续段区间", value: anchorRangeText(longest) });
  }
  return {
    mode: MODE_LINEAR,
    status: "unqualified",
    headline: "保温不合格",
    rows,
  };
}
