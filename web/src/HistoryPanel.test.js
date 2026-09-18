import { createElement as h } from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import HistoryPanel from "./HistoryPanel.jsx";
import { mapHistoryItems } from "./lib/history";

function render(state, onSelect = vi.fn()) {
  return renderToString(h(HistoryPanel, { state, onSelect }));
}

const sampleItems = mapHistoryItems([
  {
    id: 2,
    heatNo: "H-002",
    filename: "b.json",
    analyzedAt: 1800,
    qualified: false,
    recordCount: 10,
  },
  {
    id: 1,
    heatNo: null,
    filename: "a.json",
    analyzedAt: 0,
    qualified: true,
    recordCount: 61,
  },
]);

describe("HistoryPanel 列表渲染", () => {
  it("ready 且为空时只显示空态", () => {
    const html = render({ status: "ready", items: [], message: null });
    expect(html).toContain("暂无历史记录");
    expect(html).not.toContain("history-item");
  });

  it("loading 首次加载显示加载提示", () => {
    const html = render({ status: "loading", items: [], message: null });
    expect(html).toContain("正在读取最近记录");
  });

  it("ready 列表逐条映射视图模型且保持倒序", () => {
    const html = render({ status: "ready", items: sampleItems, message: null });
    // 两条都在
    expect(html).toContain("炉次 H-002");
    expect(html).toContain("未填炉次号");
    expect(html).toContain("b.json");
    expect(html).toContain("a.json");
    expect(html).toContain("不合格");
    expect(html).toContain("合格");
    expect(html).toContain("10 条记录");
    expect(html).toContain("61 条记录");
    // 摘要标明判定方式（旧记录按严格判定）
    expect(html).toContain("严格判定");
    // data-id 透传，供点击恢复使用
    expect(html).toContain('data-id="2"');
    expect(html).toContain('data-id="1"');
    // 倒序：更新的 id=2 排在前面
    expect(html.indexOf('data-id="2"')).toBeLessThan(html.indexOf('data-id="1"'));
  });

  it("线性等效模式记录在摘要中标明判定方式", () => {
    const items = mapHistoryItems([
      {
        id: 9,
        heatNo: "H-LIN",
        filename: "lin.json",
        analyzedAt: 0,
        qualified: true,
        recordCount: 100,
        analysisMode: "linear_equivalent",
      },
    ]);
    const html = render({ status: "ready", items, message: null });
    expect(html).toContain("炉次 H-LIN");
    expect(html).toContain("线性曲线等效保温");
  });

  it("后台刷新失败时保留旧列表并额外显示错误", () => {
    const html = render({
      status: "error",
      items: sampleItems,
      message: "最近记录读取失败",
    });
    expect(html).toContain("最近记录读取失败");
    expect(html).toContain("炉次 H-002"); // 旧列表保留
  });

  it("ready 列表上的恢复失败提示与列表共存", () => {
    const html = render({
      status: "ready",
      items: sampleItems,
      message: "该记录恢复失败，可能已被清理；当前结果保留未变。",
    });
    expect(html).toContain("恢复失败");
    expect(html).toContain("炉次 H-002");
  });
});
