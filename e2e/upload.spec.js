import { expect, test } from "@playwright/test";

/** 生成从 startT 起、每 step 秒一点、恒定 temp 的 count 条记录 */
function series(startT, count, step, temp) {
  return Array.from({ length: count }, (_, i) => ({
    t: startT + i * step,
    temp,
  }));
}

function uploadPayload(page, name, records) {
  return page
    .locator("#record-file")
    .setInputFiles({
      name,
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(records)),
    })
    .then(() => page.getByRole("button", { name: "上传并分析" }).click());
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

test("充分保温：页面显示合格与最早达标段起止时间", async ({ page }) => {
  // 0..1800 秒，每 30 秒一点，恒温 850°C → 时长恰为 1800 秒，合格
  await uploadPayload(page, "qualified.json", series(0, 61, 30, 850));

  const verdict = page.getByTestId("verdict");
  await expect(verdict).toBeVisible();
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");
  await expect(verdict).toContainText("达标段开始");
  await expect(verdict).toContainText("达标段结束");
  await expect(verdict).toContainText("1800 秒（30 分钟）");
  await expect(page.getByTestId("error")).toHaveCount(0);
});

test("短时到温：页面显示不合格与最长有效段时长", async ({ page }) => {
  // 仅 10 点（270 秒）在区间内 → 不合格
  await uploadPayload(page, "short.json", series(0, 10, 30, 850));

  const verdict = page.getByTestId("verdict");
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温不合格");
  await expect(verdict).toContainText("最长有效段时长");
  await expect(verdict).toContainText("270 秒（约 4.5 分钟）");
});

test("采样断档：超 60 秒间隔被切段，判不合格", async ({ page }) => {
  // 两段各 120 秒，中间断档 300 秒，温度全程在区间内
  const records = [...series(0, 5, 30, 850), ...series(420, 5, 30, 850)];
  await uploadPayload(page, "gap.json", records);

  const verdict = page.getByTestId("verdict");
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温不合格");
  await expect(verdict).toContainText("120 秒（2 分钟）");
});

test("乱序文件整份拒绝并清除旧结果", async ({ page }) => {
  // 先上传合格文件拿到结论
  await uploadPayload(page, "qualified.json", series(0, 61, 30, 850));
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");

  // 再上传时间戳乱序的文件 → 拒绝，且旧结论被清除
  const bad = [
    { t: 0, temp: 850 },
    { t: 30, temp: 851 },
    { t: 30, temp: 852 },
  ];
  await uploadPayload(page, "bad.json", bad);

  const error = page.getByTestId("error");
  await expect(error).toBeVisible();
  await expect(error).toContainText("文件被拒绝");
  await expect(page.getByTestId("verdict")).toHaveCount(0);
});

test("非有限温度值整份拒绝", async ({ page }) => {
  const raw = '[{"t": 0, "temp": 850}, {"t": 30, "temp": NaN}]';
  await page.locator("#record-file").setInputFiles({
    name: "nan.json",
    mimeType: "application/json",
    buffer: Buffer.from(raw),
  });
  await page.getByRole("button", { name: "上传并分析" }).click();

  await expect(page.getByTestId("error")).toBeVisible();
  await expect(page.getByTestId("verdict")).toHaveCount(0);
});

test("超大合法整数时间戳：页面显示保温结论与达标段", async ({ page }) => {
  // 10^13 秒超出 JS Date 可表示范围，但属于合法整数时间戳
  const base = 10_000_000_000_000;
  const records = Array.from({ length: 61 }, (_, i) => ({
    t: base + i * 30,
    temp: 850,
  }));
  await uploadPayload(page, "huge.json", records);

  const verdict = page.getByTestId("verdict");
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");
  await expect(verdict).toContainText("达标段开始");
  await expect(verdict).toContainText("t = 10000000000000");
  await expect(verdict).toContainText("t = 10000000001800");
  await expect(page.getByTestId("error")).toHaveCount(0);
});

test("超出安全整数的时间戳：起止保留相差1800秒的原始值", async ({ page }) => {
  // 10^20 超出 JS 安全整数（2^53），朴素 JSON.parse 会把起止舍入成同一时刻
  const base = 100000000000000000000n;
  const body =
    "[" +
    Array.from(
      { length: 61 },
      (_, i) => `{"t": ${base + BigInt(i * 30)}, "temp": 850}`,
    ).join(",") +
    "]";
  await page.locator("#record-file").setInputFiles({
    name: "huge-bigint.json",
    mimeType: "application/json",
    buffer: Buffer.from(body),
  });
  await page.getByRole("button", { name: "上传并分析" }).click();

  const verdict = page.getByTestId("verdict");
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");
  await expect(verdict).toContainText("t = 100000000000000000000");
  await expect(verdict).toContainText("t = 100000000000000001800");
  await expect(page.getByTestId("error")).toHaveCount(0);
});
