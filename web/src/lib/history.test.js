import { describe, expect, it } from "vitest";

import { formatAnalyzedAt, mapHistoryItem, mapHistoryItems } from "./history";

describe("mapHistoryItems 列表映射", () => {
  it("按后端顺序映射全部字段", () => {
    const items = mapHistoryItems([
      {
        id: 7,
        heatNo: "H-007",
        filename: "run-7.json",
        analyzedAt: 0,
        qualified: true,
        recordCount: 61,
        analysisMode: "linear_equivalent",
      },
      {
        id: 6,
        heatNo: null,
        filename: "run-6.json",
        analyzedAt: 1800,
        qualified: false,
        recordCount: 10,
      },
    ]);
    expect(items).toHaveLength(2);
    // 保持后端给出的分析时间倒序，前端不重排
    expect(items.map((i) => i.id)).toEqual([7, 6]);

    expect(items[0]).toMatchObject({
      id: 7,
      heatNo: "H-007",
      title: "炉次 H-007",
      filename: "run-7.json",
      qualified: true,
      statusText: "合格",
      analysisMode: "linear_equivalent",
      modeText: "等效保温",
      recordCountText: "61 条记录",
      analyzedAtText: "1970-01-01 00:00:00 UTC",
    });
    expect(items[1]).toMatchObject({
      id: 6,
      analysisMode: "strict",
      modeText: "严格判定",
    });
  });

  it("未填炉次号的记录有明确占位", () => {
    const item = mapHistoryItem({
      id: 1,
      heatNo: null,
      filename: "legacy.json",
      analyzedAt: 0,
      qualified: false,
      recordCount: 2,
    });
    expect(item.heatNo).toBeNull();
    expect(item.title).toBe("未填炉次号");
    expect(item.statusText).toBe("不合格");
  });

  it("无 analysisMode 的旧记录按严格判定标明", () => {
    const item = mapHistoryItem({
      id: 1,
      heatNo: "H-OLD",
      filename: "old.json",
      analyzedAt: 0,
      qualified: true,
      recordCount: 61,
    });
    expect(item.analysisMode).toBe("strict");
    expect(item.modeText).toBe("严格判定");
  });

  it("空白炉次号与空文件名安全兜底", () => {
    const item = mapHistoryItem({
      id: 1,
      heatNo: null,
      filename: "",
      analyzedAt: 0,
      qualified: true,
      recordCount: null,
    });
    expect(item.filename).toBe("未知文件");
    expect(item.recordCountText).toBe("");
  });

  it("非数组输入映射为空列表（空态）", () => {
    expect(mapHistoryItems(null)).toEqual([]);
    expect(mapHistoryItems(undefined)).toEqual([]);
  });
});

describe("formatAnalyzedAt 服务端分析时间", () => {
  it("epoch 秒格式化为 UTC 文本", () => {
    expect(formatAnalyzedAt(0)).toBe("1970-01-01 00:00:00 UTC");
    expect(formatAnalyzedAt(1800)).toBe("1970-01-01 00:30:00 UTC");
  });

  it("缺失或非法时间降级为占位文本而不抛异常", () => {
    expect(formatAnalyzedAt(null)).toBe("时间未知");
    expect(formatAnalyzedAt(undefined)).toBe("时间未知");
    expect(formatAnalyzedAt(NaN)).toBe("时间未知");
    expect(formatAnalyzedAt("abc")).toBe("时间未知");
  });
});
