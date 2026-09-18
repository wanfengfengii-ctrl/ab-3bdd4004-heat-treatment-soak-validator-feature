import { expect, test } from "@playwright/test";

const MODE_SELECTOR = "[data-testid=analysis-mode]";

async function uploadRecords(page, name, records, mode) {
  await page.locator("#record-file").setInputFiles({
    name,
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(records)),
  });
  if (mode) {
    await page.locator(MODE_SELECTOR).selectOption({ label: mode });
  }
  await page.getByRole("button", { name: "上传并分析" }).click();
}

// 低温穿入：0s@830 → 60s@850（840 出现在 30s），随后 850°C 恒温到 1860s
function lowTempPenetrationSeries() {
  return [
    { t: 0, temp: 830 },
    { t: 60, temp: 850 },
    ...Array.from({ length: 60 }, (_, i) => ({ t: 90 + i * 30, temp: 850 })),
  ];
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

test("默认严格判定：合格结论标明严格判定", async ({ page }) => {
  const records = Array.from({ length: 61 }, (_, i) => ({
    t: i * 30,
    temp: 850,
  }));
  await uploadRecords(page, "strict.json", records);
  const verdict = page.getByTestId("verdict");
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");
  await expect(verdict).toContainText("严格判定");
  // 选择器默认停留在严格判定
  await expect(page.locator(MODE_SELECTOR)).toHaveValue("strict");
});

test("线性等效：低温穿入后恒温达标，显示首次达标时刻与等效秒", async ({ page }) => {
  await uploadRecords(
    page,
    "penetration.json",
    lowTempPenetrationSeries(),
    "线性曲线等效保温",
  );

  const verdict = page.getByTestId("verdict");
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");
  await expect(verdict).toContainText("线性曲线等效保温");
  // 连续段从穿入 840°C 边界的时刻 t=30 开始
  await expect(verdict).toContainText("1970-01-01 00:00:00 UTC +30 秒");
  // 首次达标时刻：锚点 1830（00:30:30）+ 8.36 秒偏移
  await expect(verdict).toContainText("首次达标时刻");
  await expect(verdict).toContainText("1970-01-01 00:30:30 UTC +8.36 秒");
  // 穿入片积分 21.64 + 1800 秒恒温
  await expect(verdict).toContainText("1821.64 等效秒");
  await expect(page.getByTestId("error")).toHaveCount(0);
});

test("线性等效：单条线段自下而上穿越双边界裁剪为单片", async ({ page }) => {
  // 830@0 → 870@40：840 在 10s、860 在 30s，片 [10,30]，积分约 21.64 等效秒
  await uploadRecords(
    page,
    "cross.json",
    [
      { t: 0, temp: 830 },
      { t: 40, temp: 870 },
    ],
    "线性曲线等效保温",
  );

  const verdict = page.getByTestId("verdict");
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温不合格");
  await expect(verdict).toContainText("线性曲线等效保温");
  await expect(verdict).toContainText("21.64 等效秒");
  await expect(verdict).toContainText("1970-01-01 00:00:00 UTC +10 秒");
  await expect(verdict).toContainText("20 秒（约 0.3 分钟）");
});

test("线性等效：间隔恰为 60 秒仍连续，超过 60 秒切段", async ({ page }) => {
  await uploadRecords(
    page,
    "gap60.json",
    [
      { t: 0, temp: 850 },
      { t: 60, temp: 850 },
    ],
    "线性曲线等效保温",
  );
  let verdict = page.getByTestId("verdict");
  await expect(verdict).toContainText("60 等效秒");

  // 61 秒断档：无任何连续片，最长为 0
  await uploadRecords(
    page,
    "gap61.json",
    [
      { t: 0, temp: 850 },
      { t: 61, temp: 850 },
    ],
    "线性曲线等效保温",
  );
  verdict = page.getByTestId("verdict");
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温不合格");
  await expect(verdict).toContainText("0 等效秒");
});

test("切换模式后上传显示对应判据；回看记录标明其模式且不改上传选择", async ({
  page,
}) => {
  // 同一份数据：860°C 恒温 900 物理秒——严格不合格、线性等效合格（权重 2）
  const records = Array.from({ length: 31 }, (_, i) => ({
    t: i * 30,
    temp: 860,
  }));

  // 先以线性等效上传：合格
  await uploadRecords(page, "dual-lin.json", records, "线性曲线等效保温");
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");
  const linearItem = page
    .getByTestId("history-item")
    .filter({ hasText: "dual-lin.json" })
    .first();
  await expect(linearItem).toContainText("线性曲线等效保温");

  // 切回严格判定，换文件名重新上传：不合格——证明上传采用当前选择
  await page.locator("#record-file").setInputFiles({
    name: "dual-strict.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(records)),
  });
  await page.locator(MODE_SELECTOR).selectOption({ label: "严格判定" });
  await page.getByRole("button", { name: "上传并分析" }).click();
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温不合格");
  await expect(page.getByTestId("verdict")).toContainText("严格判定");
  await expect(page.locator(MODE_SELECTOR)).toHaveValue("strict");

  // 回看那条线性等效记录：结论标明线性模式，但上传选择仍是严格判定
  await linearItem.click();
  const verdict = page.getByTestId("verdict");
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");
  await expect(verdict).toContainText("线性曲线等效保温");
  await expect(page.getByTestId("verdict-source")).toContainText("线性曲线等效保温");
  await expect(page.locator(MODE_SELECTOR)).toHaveValue("strict");
});

test("未知模式由后端先于文件解析拒绝（可识别 422）", async ({ request }) => {
  // 文件本身也非法（时间戳重复）：必须优先报 invalid_analysis_mode
  const resp = await request.post("/api/analyze", {
    multipart: {
      file: {
        name: "bad.json",
        mimeType: "application/json",
        buffer: Buffer.from(
          JSON.stringify([{ t: 1, temp: 850 }, { t: 1, temp: 851 }]),
        ),
      },
      analysis_mode: "bogus",
    },
  });
  expect(resp.status()).toBe(422);
  const body = await resp.json();
  expect(body.detail.code).toBe("invalid_analysis_mode");
});

test("未知模式的 422 会清除旧结论", async ({ page }) => {
  // 先拿到一个有效结论
  await uploadRecords(
    page,
    "ok.json",
    Array.from({ length: 61 }, (_, i) => ({ t: i * 30, temp: 850 })),
    "线性曲线等效保温",
  );
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");

  // 模拟后端对本次上传返回未知模式 422：页面清除旧结论、显示可识别错误
  await page.route("**/api/analyze", (route) =>
    route.fulfill({
      status: 422,
      contentType: "application/json",
      body: JSON.stringify({
        detail: { code: "invalid_analysis_mode", message: "未知判定模式" },
      }),
    }),
  );
  await page.getByRole("button", { name: "上传并分析" }).click();

  const error = page.getByTestId("error");
  await expect(error).toBeVisible();
  await expect(error).toContainText("未知判定模式");
  await expect(page.getByTestId("verdict")).toHaveCount(0);
});
