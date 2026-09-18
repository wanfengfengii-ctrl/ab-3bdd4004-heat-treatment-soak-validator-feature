import { expect, test } from "@playwright/test";

/** 生成从 startT 起、每 step 秒一点、恒定 temp 的 count 条记录 */
function series(startT, count, step, temp) {
  return Array.from({ length: count }, (_, i) => ({
    t: startT + i * step,
    temp,
  }));
}

/** strict 不合格、linear 合格的低温穿入炉次（与后端判据同一形状）。 */
function slowWarmupRecords() {
  const records = [{ t: 0, temp: 835 }, { t: 60, temp: 850 }];
  for (let t = 90; t <= 1830; t += 30) records.push({ t, temp: 850 });
  records.push({ t: 1832, temp: 850 });
  return records;
}

async function uploadPayload(page, name, records, heatNo) {
  await page.locator("#record-file").setInputFiles({
    name,
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(records)),
  });
  if (heatNo !== undefined) await page.locator("#heat-no").fill(heatNo);
  await page.getByRole("button", { name: "上传并分析" }).click();
}

async function pickMode(page, mode) {
  await page.locator(`input[name="analysis-mode"][value="${mode}"]`).check();
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

test("默认选中严格判定", async ({ page }) => {
  await expect(
    page.locator('input[name="analysis-mode"][value="strict"]'),
  ).toBeChecked();
  await expect(
    page.locator('input[name="analysis-mode"][value="linear_equivalent"]'),
  ).not.toBeChecked();
});

test("切换线性模式：低温穿入炉次按等效秒判合格并显示首次达标偏移", async ({ page }) => {
  // strict 下该炉次带内采样跨度仅 1772 秒，不合格；linear 累计等效秒达标
  await pickMode(page, "linear_equivalent");
  await uploadPayload(page, "slow-warmup.json", slowWarmupRecords(), "H-LIN-E2E");

  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");
  const verdict = page.getByTestId("verdict");
  await expect(page.getByTestId("verdict-mode")).toContainText(
    "判定方式：线性曲线等效保温",
  );
  // 段起点在穿越边与 840°C 的交点：锚点 0 + 20 秒
  await expect(verdict).toContainText("达标段开始");
  await expect(verdict).toContainText("1970-01-01 00:00:00 UTC + 20 秒");
  // 首次达标时刻为整数锚点 1830 + 十进制偏移
  await expect(verdict).toContainText("首次达标时刻");
  await expect(verdict).toContainText("1970-01-01 00:30:30 UTC + 1.146 秒");
  await expect(verdict).toContainText("1800 等效秒（30 分钟）");
  // 摘要标明等效保温
  await expect(
    page.getByTestId("history-item").filter({ hasText: "H-LIN-E2E" }).first(),
  ).toContainText("等效保温");
  await expect(page.getByTestId("error")).toHaveCount(0);
});

test("切回严格模式：同一文件改按物理时长判不合格", async ({ page }) => {
  await pickMode(page, "linear_equivalent");
  await pickMode(page, "strict");
  await uploadPayload(page, "slow-warmup.json", slowWarmupRecords(), "H-STRICT-E2E");

  await expect(page.getByTestId("verdict-headline")).toHaveText("保温不合格");
  await expect(page.getByTestId("verdict-mode")).toContainText("判定方式：严格判定");
  await expect(page.getByTestId("verdict")).toContainText(
    "1772 秒（约 29.5 分钟）",
  );
  await expect(
    page.getByTestId("history-item").filter({ hasText: "H-STRICT-E2E" }).first(),
  ).toContainText("严格判定");
});

test("线性模式：单线段穿越双边界按裁剪片积分", async ({ page }) => {
  // 60 秒内 835→865°C：t=10 穿入 840、t=50 穿出 860，
  // 带内片积分 = 30/ln2 ≈ 43.281 等效秒
  await pickMode(page, "linear_equivalent");
  await uploadPayload(page, "cross.json", [
    { t: 0, temp: 835 },
    { t: 60, temp: 865 },
  ]);

  await expect(page.getByTestId("verdict-headline")).toHaveText("保温不合格");
  const verdict = page.getByTestId("verdict");
  await expect(verdict).toContainText("43.281 等效秒");
  await expect(verdict).toContainText("+ 10 秒");
  await expect(verdict).toContainText("+ 50 秒");
});

test("线性模式：恰 60 秒间隔保持连续，超 60 秒间隔切段", async ({ page }) => {
  await pickMode(page, "linear_equivalent");

  // 0,60,120：相邻间隔恰 60 秒 → 一段 120 等效秒
  await uploadPayload(page, "gap60.json", series(0, 3, 60, 850));
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温不合格");
  await expect(page.getByTestId("verdict")).toContainText("120 等效秒（2 分钟）");

  // 0,60,121：后一条间隔 61 秒断档，最长只剩 0→60 的 60 等效秒
  await uploadPayload(page, "gap61.json", [
    { t: 0, temp: 850 },
    { t: 60, temp: 850 },
    { t: 121, temp: 850 },
  ]);
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温不合格");
  await expect(page.getByTestId("verdict")).toContainText("60 等效秒（1 分钟）");
});

test("回看线性记录标明记录模式，且不改动当前上传选择", async ({ page }) => {
  // 先以线性模式上传并判合格
  await pickMode(page, "linear_equivalent");
  await uploadPayload(page, "slow-warmup.json", slowWarmupRecords(), "H-LIN-REVIEW");
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");

  // 把上传选择切回严格（不上传）
  await pickMode(page, "strict");

  // 回看刚写入的线性记录
  await page
    .getByTestId("history-item")
    .filter({ hasText: "H-LIN-REVIEW" })
    .first()
    .click();

  // 结论按该记录的线性模式呈现
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");
  await expect(page.getByTestId("verdict-mode")).toContainText(
    "历史记录判定方式：线性曲线等效保温",
  );
  await expect(page.getByTestId("verdict")).toContainText("1800 等效秒（30 分钟）");
  await expect(page.getByTestId("verdict-source")).toContainText("H-LIN-REVIEW");
  // 上传选择不被回看改动：仍是严格判定
  await expect(
    page.locator('input[name="analysis-mode"][value="strict"]'),
  ).toBeChecked();
});

test("无模式旧历史记录回看错按严格判定呈现", async ({ page }) => {
  const legacyConclusion = {
    recordCount: 61,
    segmentCount: 1,
    qualified: true,
    earliestQualifyingSegment: { startT: 0, endT: 1800, duration: 1800, points: 61 },
    longestSegment: { startT: 0, endT: 1800, duration: 1800, points: 61 },
    limits: {
      tempLow: 840,
      tempHigh: 860,
      maxGapSeconds: 60,
      minSoakSeconds: 1800,
    },
  };
  await page.route("**/api/history", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        json: {
          items: [
            {
              id: 987654,
              heatNo: "H-OLD-NO-MODE",
              filename: "legacy.json",
              analyzedAt: 0,
              qualified: true,
              recordCount: 61,
            },
          ],
        },
      });
    } else {
      await route.continue();
    }
  });
  await page.route("**/api/history/*", (route) =>
    route.fulfill({
      json: {
        id: 987654,
        heatNo: "H-OLD-NO-MODE",
        filename: "legacy.json",
        analyzedAt: 0,
        conclusion: legacyConclusion,
      },
    }),
  );
  await page.goto("/");

  // 上传选择先选成线性，回看旧记录不得改动它
  await pickMode(page, "linear_equivalent");
  await page
    .getByTestId("history-item")
    .filter({ hasText: "H-OLD-NO-MODE" })
    .first()
    .click();

  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");
  // 无模式旧记录按严格判定标明，并用 strict 字段渲染
  await expect(page.getByTestId("verdict-mode")).toContainText(
    "历史记录判定方式：严格判定",
  );
  await expect(page.getByTestId("verdict")).toContainText("达标段时长");
  await expect(
    page.locator('input[name="analysis-mode"][value="linear_equivalent"]'),
  ).toBeChecked();
});

test("未知判定方式先于文件解析返回可识别 422", async ({ request }) => {
  const buffer = Buffer.from(JSON.stringify(series(0, 2, 30, 850)));
  const resp = await request.post("/api/analyze", {
    multipart: {
      file: { name: "data.json", mimeType: "application/json", buffer },
      analysis_mode: "equivalent",
    },
  });
  expect(resp.status()).toBe(422);
  const body = await resp.json();
  expect(body.detail.code).toBe("unknown_analysis_mode");
  expect(typeof body.detail.message).toBe("string");

  // 即便文件本身也不合法，模式校验仍先于文件解析：错误码保持模式错误
  const badBuffer = Buffer.from(JSON.stringify([{ t: 1, temp: 850 }, { t: 1, temp: 850 }]));
  const respBad = await request.post("/api/analyze", {
    multipart: {
      file: { name: "bad.json", mimeType: "application/json", buffer: badBuffer },
      analysis_mode: "bogus",
    },
  });
  expect(respBad.status()).toBe(422);
  expect((await respBad.json()).detail.code).toBe("unknown_analysis_mode");
});

test("不带 analysis_mode 的旧请求仍按严格判定成功", async ({ request }) => {
  const buffer = Buffer.from(JSON.stringify(series(0, 61, 30, 850)));
  const resp = await request.post("/api/analyze", {
    multipart: {
      file: { name: "legacy-client.json", mimeType: "application/json", buffer },
    },
  });
  expect(resp.status()).toBe(200);
  const body = await resp.json();
  expect(body.analysisMode).toBe("strict");
  expect(body.qualified).toBe(true);
});
