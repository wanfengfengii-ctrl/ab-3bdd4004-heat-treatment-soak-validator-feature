// 纯函数：把 API 分析结果映射为页面上的“唯一结论”视图模型，
// 以及上传前的本地预检。判定本身由后端完成，这里只做呈现决策。

export const MAX_FILE_BYTES = 2 * 1024 * 1024; // 与后端 2 MiB 上限一致

// 判定模式（与后端 analysis_mode 对应）；任何无模式字段的旧记录按严格判定读取
export const MODE_STRICT = "strict";
export const MODE_LINEAR_EQUIVALENT = "linear_equivalent";

export const ANALYSIS_MODES = [
  { value: MODE_STRICT, label: "严格判定" },
  { value: MODE_LINEAR_EQUIVALENT, label: "线性曲线等效保温" },
];

export function modeLabel(mode) {
  return ANALYSIS_MODES.find((m) => m.value === mode)?.label ?? "严格判定";
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
 * 插值时刻行：整数秒锚点 t 加十进制秒偏移 offset（线性等效模式裁剪出的
 * 片端点/首次达标时刻）。锚点可格式化时显示 UTC 时间并附偏移，超范围时
 * 退回“t = 锚点 + 偏移秒”，BigInt 锚点与小偏移相加不会丢失精度。
 */
function pointRow(label, t, offset) {
  const off = Number(offset);
  const hasOffset = Number.isFinite(off) && Math.abs(off) > 1e-9;
  const offText = hasOffset ? ` +${parseFloat(off.toFixed(3))} 秒` : "";
  const formatted = formatTimestamp(t);
  if (formatted === null) {
    return { label, value: `t = ${t}${offText}` };
  }
  return { label, value: `${formatted}${offText}`, raw: hasOffset ? undefined : t };
}

/** 等效秒：最多保留三位小数，并标注“等效秒”。 */
export function formatEquivalentSeconds(seconds) {
  const n = Number(seconds);
  if (!Number.isFinite(n)) return "—";
  return `${parseFloat(n.toFixed(3))} 等效秒`;
}

/**
 * 由 API 分析结果生成唯一结论：
 * - 严格判定：合格给最早达标段起止时间；不合格给最长有效段时长。
 * - 线性等效：合格给连续段起点与“首次达标时刻”（解析反函数求出）；
 *   不合格给累计等效秒最多的连续段。两种模式都在结论中标注判定方式。
 */
export function buildVerdict(analysis) {
  const mode = analysis.analysisMode ?? MODE_STRICT;
  if (mode === MODE_LINEAR_EQUIVALENT) {
    return buildLinearVerdict(analysis);
  }
  return buildStrictVerdict(analysis);
}

function buildStrictVerdict(analysis) {
  if (analysis.qualified && analysis.earliestQualifyingSegment) {
    const seg = analysis.earliestQualifyingSegment;
    return {
      status: "qualified",
      mode: MODE_STRICT,
      headline: "保温合格",
      rows: [
        { label: "判定方式", value: modeLabel(MODE_STRICT) },
        timeRow("达标段开始", seg.startT),
        timeRow("达标段结束", seg.endT),
        { label: "达标段时长", value: formatDuration(seg.duration) },
      ],
    };
  }
  const longest = analysis.longestSegment;
  const duration = longest ? longest.duration : 0;
  const rows = [
    { label: "判定方式", value: modeLabel(MODE_STRICT) },
    { label: "最长有效段时长", value: formatDuration(duration) },
  ];
  if (longest) {
    rows.push({
      label: "最长有效段区间",
      value: `${formatTimestampOrRaw(longest.startT)} 至 ${formatTimestampOrRaw(longest.endT)}`,
    });
  }
  return { status: "unqualified", mode: MODE_STRICT, headline: "保温不合格", rows };
}

function buildLinearVerdict(analysis) {
  const modeRow = { label: "判定方式", value: modeLabel(MODE_LINEAR_EQUIVALENT) };
  if (analysis.qualified && analysis.earliestQualifyingSegment) {
    const seg = analysis.earliestQualifyingSegment;
    return {
      status: "qualified",
      mode: MODE_LINEAR_EQUIVALENT,
      headline: "保温合格",
      rows: [
        modeRow,
        pointRow("达标段开始", seg.startT, seg.startOffset ?? 0),
        pointRow("首次达标时刻", seg.reachT, seg.reachOffset ?? 0),
        {
          label: "累计等效保温",
          value: formatEquivalentSeconds(seg.equivalentSeconds),
        },
      ],
    };
  }
  const longest = analysis.longestSegment;
  const rows = [
    modeRow,
    {
      label: "最长连续段等效保温",
      value: formatEquivalentSeconds(longest ? longest.equivalentSeconds : 0),
    },
  ];
  if (longest) {
    rows.push(
      pointRow("连续段起点", longest.startT, longest.startOffset ?? 0),
    );
    rows.push({
      label: "连续段时长",
      value: formatDuration(
        Math.round((longest.duration + Number.EPSILON) * 1000) / 1000,
      ),
    });
  }
  return {
    status: "unqualified",
    mode: MODE_LINEAR_EQUIVALENT,
    headline: "保温不合格",
    rows,
  };
}
