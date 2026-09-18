import { describe, expect, it } from "vitest";

import {
  MAX_FILE_BYTES,
  MODE_LINEAR_EQUIVALENT,
  MODE_STRICT,
  buildVerdict,
  formatDuration,
  formatEquivalentSeconds,
  formatTimestamp,
  formatTimestampOrRaw,
  modeLabel,
  parseJsonPreserveBigInts,
  validateFile,
} from "./verdict";

const row = (verdict, label) => verdict.rows.find((r) => r.label === label);

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
    expect(row(verdict, "判定方式").value).toBe("严格判定");
    const start = row(verdict, "达标段开始");
    const end = row(verdict, "达标段结束");
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
    expect(row(verdict, "判定方式").value).toBe("严格判定");
    expect(row(verdict, "最长有效段时长").value).toBe("270 秒（约 4.5 分钟）");
  });

  it("短时到温（无有效段）时长为 0", () => {
    const verdict = buildVerdict({
      qualified: false,
      earliestQualifyingSegment: null,
      longestSegment: null,
    });
    expect(verdict.status).toBe("unqualified");
    expect(row(verdict, "最长有效段时长").value).toBe("0 秒（0 分钟）");
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

describe("modeLabel / 默认判定模式", () => {
  it("已知模式给出中文标签", () => {
    expect(modeLabel(MODE_STRICT)).toBe("严格判定");
    expect(modeLabel(MODE_LINEAR_EQUIVALENT)).toBe("线性曲线等效保温");
  });

  it("未知/缺失模式兜底为严格判定", () => {
    expect(modeLabel("weird")).toBe("严格判定");
    const verdict = buildVerdict({
      qualified: false,
      earliestQualifyingSegment: null,
      longestSegment: null,
    });
    expect(verdict.mode).toBe(MODE_STRICT);
  });
});

describe("formatEquivalentSeconds 等效秒", () => {
  it("整数等效秒不带尾零", () => {
    expect(formatEquivalentSeconds(1800)).toBe("1800 等效秒");
  });
  it("小数等效秒保留至多三位", () => {
    expect(formatEquivalentSeconds(21.6404256)).toBe("21.64 等效秒");
  });
});

describe("buildVerdict 线性曲线等效保温", () => {
  const linearQualified = {
    analysisMode: MODE_LINEAR_EQUIVALENT,
    qualified: true,
    earliestQualifyingSegment: {
      startT: 0,
      startOffset: 30,
      endT: 1860,
      endOffset: 0,
      duration: 1830,
      equivalentSeconds: 1821.6404,
      slices: 61,
      reachT: 1830,
      reachOffset: 8.359574,
    },
    longestSegment: null,
  };

  it("合格时标注判定方式、段起点、首次达标时刻与等效秒", () => {
    const verdict = buildVerdict(linearQualified);
    expect(verdict.status).toBe("qualified");
    expect(verdict.mode).toBe(MODE_LINEAR_EQUIVALENT);
    expect(row(verdict, "判定方式").value).toBe("线性曲线等效保温");
    expect(row(verdict, "达标段开始").value).toBe("1970-01-01 00:00:00 UTC +30 秒");
    expect(row(verdict, "首次达标时刻").value).toBe(
      "1970-01-01 00:30:30 UTC +8.36 秒",
    );
    expect(row(verdict, "累计等效保温").value).toBe("1821.64 等效秒");
    // 不再出现严格模式的“达标段结束/达标段时长”行
    expect(row(verdict, "达标段结束")).toBeUndefined();
  });

  it("偏移为 0 时不附偏移文本", () => {
    const verdict = buildVerdict({
      analysisMode: MODE_LINEAR_EQUIVALENT,
      qualified: true,
      earliestQualifyingSegment: {
        startT: 0, startOffset: 0, endT: 900, endOffset: 0,
        duration: 900, equivalentSeconds: 1800, slices: 31,
        reachT: 900, reachOffset: 0,
      },
      longestSegment: null,
    });
    expect(row(verdict, "首次达标时刻").value).toBe("1970-01-01 00:15:00 UTC");
  });

  it("不合格时给出最长连续段等效保温", () => {
    const verdict = buildVerdict({
      analysisMode: MODE_LINEAR_EQUIVALENT,
      qualified: false,
      earliestQualifyingSegment: null,
      longestSegment: {
        startT: 0, startOffset: 10, endT: 30, endOffset: 0,
        duration: 20, equivalentSeconds: 21.6404, slices: 1,
      },
    });
    expect(verdict.status).toBe("unqualified");
    expect(row(verdict, "判定方式").value).toBe("线性曲线等效保温");
    expect(row(verdict, "最长连续段等效保温").value).toBe("21.64 等效秒");
    expect(row(verdict, "连续段起点").value).toBe("1970-01-01 00:00:00 UTC +10 秒");
    expect(row(verdict, "连续段时长").value).toBe("20 秒（约 0.3 分钟）");
  });

  it("不合格且无任何连续段时等效秒为 0", () => {
    const verdict = buildVerdict({
      analysisMode: MODE_LINEAR_EQUIVALENT,
      qualified: false,
      earliestQualifyingSegment: null,
      longestSegment: null,
    });
    expect(verdict.status).toBe("unqualified");
    expect(row(verdict, "最长连续段等效保温").value).toBe("0 等效秒");
  });

  it("超大锚点加十进制偏移不丢精度，退回原始锚点展示", () => {
    const base = 100000000000000000000n;
    const verdict = buildVerdict({
      analysisMode: MODE_LINEAR_EQUIVALENT,
      qualified: true,
      earliestQualifyingSegment: {
        startT: base, startOffset: 30, endT: base + 1800n, endOffset: 0,
        duration: 1770, equivalentSeconds: 1800, slices: 60,
        reachT: base + 1770n, reachOffset: 8.359,
      },
      longestSegment: null,
    });
    expect(row(verdict, "达标段开始").value).toBe(
      "t = 100000000000000000000 +30 秒",
    );
    expect(row(verdict, "首次达标时刻").value).toBe(
      "t = 100000000000000001770 +8.359 秒",
    );
  });

  it("number 型超大锚点同样退回原始秒数且保留偏移", () => {
    const base = 10_000_000_000_000;
    const verdict = buildVerdict({
      analysisMode: MODE_LINEAR_EQUIVALENT,
      qualified: true,
      earliestQualifyingSegment: {
        startT: base, startOffset: 0, endT: base + 900, endOffset: 0,
        duration: 900, equivalentSeconds: 1800, slices: 31,
        reachT: base + 900, reachOffset: 0,
      },
      longestSegment: null,
    });
    expect(row(verdict, "首次达标时刻").value).toBe("t = 10000000000900");
  });
});
