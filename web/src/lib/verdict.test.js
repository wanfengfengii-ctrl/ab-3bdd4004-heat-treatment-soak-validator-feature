import { describe, expect, it } from "vitest";

import {
  MAX_FILE_BYTES,
  MODE_LINEAR,
  MODE_STRICT,
  buildVerdict,
  formatDuration,
  formatEquivalent,
  formatOffset,
  formatTimestamp,
  formatTimestampOrRaw,
  modeLabel,
  parseJsonPreserveBigInts,
  resolveAnalysisMode,
  validateFile,
} from "./verdict";

describe("validateFile 上传预检", () => {
  it("无文件时拒绝", () => {
    expect(validateFile(null).code).toBe("no_file");
  });

  it("空文件拒绝", () => {
    expect(validateFile({ size: 0 }).code).toBe("empty_file");
  });

  it("超过 2 MiB 拒绝", () => {
    const result = validateFile({ size: MAX_FILE_BYTES + 1 });
    expect(result.code).toBe("file_too_large");
  });

  it("恰好 2 MiB 放行", () => {
    expect(validateFile({ size: MAX_FILE_BYTES })).toBeNull();
  });
});

describe("parseJsonPreserveBigInts 大整数解析", () => {
  it("超出安全整数的时间戳还原为精确 BigInt", () => {
    const parsed = parseJsonPreserveBigInts(
      '{"startT": 100000000000000000000, "endT": 100000000000000001800, "duration": 1800, "temp": 840.5}',
    );
    expect(parsed.startT).toBe(100000000000000000000n);
    expect(parsed.endT).toBe(100000000000000001800n);
    // 朴素 JSON.parse 会把这两个值舍入成同一个数
    expect(parsed.endT - parsed.startT).toBe(1800n);
  });

  it("安全整数与普通浮点保持 number 不变", () => {
    const parsed = parseJsonPreserveBigInts(
      '{"t": 1800, "temp": 840.5, "low": 840.0, "ok": true, "s": "x"}',
    );
    expect(parsed.t).toBe(1800);
    expect(typeof parsed.t).toBe("number");
    expect(parsed.temp).toBe(840.5);
    expect(parsed.low).toBe(840);
    expect(parsed.ok).toBe(true);
    expect(parsed.s).toBe("x");
  });
});

describe("formatTimestamp / formatDuration", () => {
  it("时间戳格式化为 UTC 文本", () => {
    expect(formatTimestamp(0)).toBe("1970-01-01 00:00:00 UTC");
    expect(formatTimestamp(1800)).toBe("1970-01-01 00:30:00 UTC");
  });

  it("超出 JS Date 范围的超大合法时间戳返回 null 而非抛异常", () => {
    // 10^13 秒 × 1000 = 10^16 毫秒 > 8.64e15，toISOString 会抛 RangeError
    expect(formatTimestamp(10_000_000_000_000)).toBeNull();
    expect(formatTimestamp(-10_000_000_000_000)).toBeNull();
    expect(formatTimestamp(Infinity)).toBeNull();
  });

  it("formatTimestampOrRaw 对超大时间戳退回原始秒数", () => {
    expect(formatTimestampOrRaw(10_000_000_000_000)).toBe("t = 10000000000000");
    expect(formatTimestampOrRaw(1800)).toBe("1970-01-01 00:30:00 UTC");
  });

  it("BigInt 时间戳精确保留原始值", () => {
    expect(formatTimestamp(100000000000000000000n)).toBeNull();
    expect(formatTimestampOrRaw(100000000000000000000n)).toBe(
      "t = 100000000000000000000",
    );
  });

  it("BigInt 时长精确格式化", () => {
    expect(formatDuration(1800n)).toBe("1800 秒（30 分钟）");
    expect(formatDuration(90n)).toBe("90 秒（约 1.5 分钟）");
  });

  it("整分钟时长不带“约”", () => {
    expect(formatDuration(1800)).toBe("1800 秒（30 分钟）");
  });

  it("非整分钟时长带“约”", () => {
    expect(formatDuration(270)).toBe("270 秒（约 4.5 分钟）");
    expect(formatDuration(0)).toBe("0 秒（0 分钟）");
  });
});

describe("buildVerdict 唯一结论", () => {
  it("合格时给出最早达标段的起止时间", () => {
    const verdict = buildVerdict({
      qualified: true,
      earliestQualifyingSegment: { startT: 0, endT: 1800, duration: 1800, points: 61 },
      longestSegment: { startT: 2000, endT: 3900, duration: 1900, points: 64 },
    });
    expect(verdict.status).toBe("qualified");
    expect(verdict.headline).toBe("保温合格");
    const start = verdict.rows.find((r) => r.label === "达标段开始");
    const end = verdict.rows.find((r) => r.label === "达标段结束");
    expect(start.value).toBe("1970-01-01 00:00:00 UTC");
    expect(end.value).toBe("1970-01-01 00:30:00 UTC");
  });

  it("不合格时给出最长有效段时长", () => {
    const verdict = buildVerdict({
      qualified: false,
      earliestQualifyingSegment: null,
      longestSegment: { startT: 300, endT: 570, duration: 270, points: 10 },
    });
    expect(verdict.status).toBe("unqualified");
    expect(verdict.headline).toBe("保温不合格");
    expect(verdict.rows[0].label).toBe("最长有效段时长");
    expect(verdict.rows[0].value).toBe("270 秒（约 4.5 分钟）");
  });

  it("短时到温（无有效段）时长为 0", () => {
    const verdict = buildVerdict({
      qualified: false,
      earliestQualifyingSegment: null,
      longestSegment: null,
    });
    expect(verdict.status).toBe("unqualified");
    expect(verdict.rows[0].value).toBe("0 秒（0 分钟）");
  });

  it("qualified 但缺段数据时按不合格兜底", () => {
    const verdict = buildVerdict({
      qualified: true,
      earliestQualifyingSegment: null,
      longestSegment: null,
    });
    expect(verdict.status).toBe("unqualified");
  });

  it("超大整数时间戳的合格段不抛异常，起止退回原始秒数", () => {
    const base = 10_000_000_000_000;
    const verdict = buildVerdict({
      qualified: true,
      earliestQualifyingSegment: {
        startT: base,
        endT: base + 1800,
        duration: 1800,
        points: 61,
      },
      longestSegment: null,
    });
    expect(verdict.status).toBe("qualified");
    const start = verdict.rows.find((r) => r.label === "达标段开始");
    const end = verdict.rows.find((r) => r.label === "达标段结束");
    expect(start.value).toBe("t = 10000000000000");
    expect(start.raw).toBeUndefined();
    expect(end.value).toBe("t = 10000000001800");
  });

  it("超大整数时间戳的不合格区间同样安全降级", () => {
    const base = 10_000_000_000_000;
    const verdict = buildVerdict({
      qualified: false,
      earliestQualifyingSegment: null,
      longestSegment: { startT: base, endT: base + 270, duration: 270, points: 10 },
    });
    expect(verdict.status).toBe("unqualified");
    const range = verdict.rows.find((r) => r.label === "最长有效段区间");
    expect(range.value).toBe("t = 10000000000000 至 t = 10000000000270");
  });

  it("超出安全整数的 BigInt 起止保留相差 1800 秒的原始值", () => {
    const base = 100000000000000000000n; // 10^20，朴素 JSON.parse 会舍入碰撞
    const verdict = buildVerdict({
      qualified: true,
      earliestQualifyingSegment: {
        startT: base,
        endT: base + 1800n,
        duration: 1800,
        points: 61,
      },
      longestSegment: null,
    });
    expect(verdict.status).toBe("qualified");
    const start = verdict.rows.find((r) => r.label === "达标段开始");
    const end = verdict.rows.find((r) => r.label === "达标段结束");
    expect(start.value).toBe("t = 100000000000000000000");
    expect(end.value).toBe("t = 100000000000000001800");
    expect(start.value).not.toBe(end.value);
  });
});

describe("resolveAnalysisMode / modeLabel", () => {
  it("linear_equivalent 识别为线性模式", () => {
    expect(resolveAnalysisMode({ analysisMode: "linear_equivalent" })).toBe(MODE_LINEAR);
  });

  it("strict、缺字段、未知值与空对象一律按严格判定", () => {
    expect(resolveAnalysisMode({ analysisMode: "strict" })).toBe(MODE_STRICT);
    expect(resolveAnalysisMode({})).toBe(MODE_STRICT);
    expect(resolveAnalysisMode(null)).toBe(MODE_STRICT);
    expect(resolveAnalysisMode({ analysisMode: "weird" })).toBe(MODE_STRICT);
  });

  it("模式文案", () => {
    expect(modeLabel(MODE_STRICT)).toBe("严格判定");
    expect(modeLabel(MODE_LINEAR)).toBe("线性曲线等效保温");
  });
});

describe("formatOffset / formatEquivalent", () => {
  it("秒偏移保留至多三位小数并去尾零", () => {
    expect(formatOffset(0)).toBe("0");
    expect(formatOffset(10)).toBe("10");
    expect(formatOffset(15.428607)).toBe("15.429");
    expect(formatOffset(15.4)).toBe("15.4");
  });

  it("非法偏移兜底为 0", () => {
    expect(formatOffset(undefined)).toBe("0");
    expect(formatOffset(NaN)).toBe("0");
  });

  it("等效秒文案", () => {
    expect(formatEquivalent(1800)).toBe("1800 等效秒（30 分钟）");
    expect(formatEquivalent(43.280851)).toBe("43.281 等效秒（约 0.7 分钟）");
  });
});

describe("buildVerdict 线性曲线等效保温", () => {
  const linearQualified = {
    analysisMode: "linear_equivalent",
    qualified: true,
    earliestQualifyingSegment: {
      startAnchorT: 0,
      startOffset: 20,
      endAnchorT: 1830,
      endOffset: 9.854,
      equivalentSeconds: 1800,
      physicalSeconds: 1819.854,
      points: 61,
    },
    longestSegment: null,
  };

  it("合格时给出锚点+偏移的开始与首次达标时刻、累计等效秒", () => {
    const v = buildVerdict(linearQualified);
    expect(v.mode).toBe(MODE_LINEAR);
    expect(v.status).toBe("qualified");
    const start = v.rows.find((r) => r.label === "达标段开始");
    const hit = v.rows.find((r) => r.label === "首次达标时刻");
    const eq = v.rows.find((r) => r.label === "累计等效秒");
    expect(start.value).toBe("1970-01-01 00:00:00 UTC + 20 秒");
    expect(hit.value).toBe("1970-01-01 00:30:30 UTC + 9.854 秒");
    expect(hit.raw).toBeUndefined();
    expect(eq.value).toBe("1800 等效秒（30 分钟）");
    const physical = v.rows.find((r) => r.label === "段内物理时长");
    expect(physical.value).toContain("1819.854");
  });

  it("偏移为 0 时不追加偏移后缀，仍附原始锚点", () => {
    const v = buildVerdict({
      analysisMode: "linear_equivalent",
      qualified: true,
      earliestQualifyingSegment: {
        startAnchorT: 0, startOffset: 0,
        endAnchorT: 900, endOffset: 0,
        equivalentSeconds: 1800, physicalSeconds: 900, points: 16,
      },
      longestSegment: null,
    });
    const start = v.rows.find((r) => r.label === "达标段开始");
    expect(start.value).toBe("1970-01-01 00:00:00 UTC");
    expect(start.raw).toBe(0);
  });

  it("不合格时给出最长连续段等效秒与锚点区间", () => {
    const v = buildVerdict({
      analysisMode: "linear_equivalent",
      qualified: false,
      earliestQualifyingSegment: null,
      longestSegment: {
        startAnchorT: 0, startOffset: 10,
        endAnchorT: 0, endOffset: 50,
        equivalentSeconds: 43.280851, physicalSeconds: 40, points: 2,
      },
    });
    expect(v.mode).toBe(MODE_LINEAR);
    expect(v.status).toBe("unqualified");
    expect(v.rows[0].label).toBe("最长连续段等效秒");
    expect(v.rows[0].value).toBe("43.281 等效秒（约 0.7 分钟）");
    const range = v.rows.find((r) => r.label === "最长连续段区间");
    expect(range.value).toContain("1970-01-01 00:00:00 UTC + 10 秒");
    expect(range.value).toContain("+ 50 秒");
  });

  it("无任何连续段时等效秒为 0", () => {
    const v = buildVerdict({
      analysisMode: "linear_equivalent",
      qualified: false,
      earliestQualifyingSegment: null,
      longestSegment: null,
    });
    expect(v.rows[0].value).toBe("0 等效秒（0 分钟）");
  });

  it("超大 BigInt 锚点 + 十进制偏移精确保留", () => {
    const base = 100000000000000000000n;
    const v = buildVerdict({
      analysisMode: "linear_equivalent",
      qualified: true,
      earliestQualifyingSegment: {
        startAnchorT: base, startOffset: 10,
        endAnchorT: base, endOffset: 50,
        equivalentSeconds: 43.280851, physicalSeconds: 40, points: 2,
      },
      longestSegment: null,
    });
    const start = v.rows.find((r) => r.label === "达标段开始");
    const hit = v.rows.find((r) => r.label === "首次达标时刻");
    expect(start.value).toBe("t = 100000000000000000000 + 10 秒");
    expect(hit.value).toBe("t = 100000000000000000000 + 50 秒");
  });
});

describe("buildVerdict 旧记录无模式按严格判定", () => {
  it("缺 analysisMode 的合格结论按 strict 字段渲染", () => {
    const v = buildVerdict({
      qualified: true,
      earliestQualifyingSegment: { startT: 0, endT: 1800, duration: 1800, points: 61 },
      longestSegment: null,
    });
    expect(v.mode).toBe(MODE_STRICT);
    expect(v.rows.find((r) => r.label === "达标段时长").value).toBe("1800 秒（30 分钟）");
  });
});
