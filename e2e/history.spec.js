import { execFileSync } from "node:child_process";

import { expect, test } from "@playwright/test";

/** 生成从 startT 起、每 step 秒一点、恒定 temp 的 count 条记录 */
function series(startT, count, step, temp) {
  return Array.from({ length: count }, (_, i) => ({
    t: startT + i * step,
    temp,
  }));
}

async function uploadPayload(page, name, records, heatNo) {
  await page.locator("#record-file").setInputFiles({
    name,
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(records)),
  });
  if (heatNo !== undefined) {
    await page.locator("#heat-no").fill(heatNo);
  }
  await page.getByRole("button", { name: "上传并分析" }).click();
}

// 命名卷会在多次 compose up 之间保留记录，因此对真实列表不断言总数，
// 只按炉次号过滤；倒序保证 first() 命中本次运行刚写入的那条
function heatItem(page, heatNo) {
  return page
    .getByTestId("history-item")
    .filter({ hasText: heatNo })
    .first();
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

test("空态：无历史记录时列表区域给出明确空态提示", async ({ page }) => {
  // 用路由把历史接口钉成空列表，保证判据不依赖卷内的残留数据
  await page.route("**/api/history", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { items: [] } });
    } else {
      await route.continue();
    }
  });
  await page.goto("/");

  await expect(page.getByTestId("history-empty")).toBeVisible();
  // 空态只出现在列表区域，上传区照常可用，结论区为空
  await expect(page.locator("#record-file")).toBeVisible();
  await expect(page.locator("#heat-no")).toBeVisible();
  await expect(page.getByTestId("verdict")).toHaveCount(0);
});

test("列表映射：上传后摘要按倒序出现，选择旧记录可恢复当时唯一结论", async ({ page }) => {
  // 先上传一条合格记录
  await uploadPayload(page, "heat1.json", series(0, 61, 30, 850), "H-E2E-1");
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");
  await expect(heatItem(page, "H-E2E-1")).toBeVisible();
  await expect(heatItem(page, "H-E2E-1")).toContainText("heat1.json");
  await expect(heatItem(page, "H-E2E-1").getByTestId("history-status")).toHaveText(
    "合格",
  );

  // 再上传一条不合格记录
  await uploadPayload(page, "heat2.json", series(0, 10, 30, 850), "H-E2E-2");
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温不合格");

  // 按分析时间倒序：最新的不合格记录排在列表第一位
  await expect(page.getByTestId("history-item").first()).toContainText("H-E2E-2");
  await expect(
    page.getByTestId("history-item").first().getByTestId("history-status"),
  ).toHaveText("不合格");

  // 点击较早的合格记录 → 恢复当时唯一结论
  await heatItem(page, "H-E2E-1").click();
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");
  const verdict = page.getByTestId("verdict");
  await expect(verdict).toContainText("1800 秒（30 分钟）");
  await expect(page.getByTestId("verdict-source")).toContainText("H-E2E-1");
  await expect(page.getByTestId("verdict-source")).toContainText("heat1.json");
  await expect(page.getByTestId("error")).toHaveCount(0);
});

test("未填炉次号的旧请求：列表项显示占位，点击仍可恢复", async ({ page }) => {
  await uploadPayload(page, "legacy.json", series(0, 61, 30, 850));
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");
  const item = page
    .getByTestId("history-item")
    .filter({ hasText: "legacy.json" })
    .first();
  await expect(item).toContainText("未填炉次号");

  await item.click();
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");
  await expect(page.getByTestId("verdict-source")).toContainText("legacy.json");
});

test("历史查询失败只在列表区域提示，当前结论保留", async ({ page }) => {
  // GET 历史列表始终失败；POST 分析不受影响
  await page.route("**/api/history", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ status: 500, body: "db down" });
    } else {
      await route.continue();
    }
  });
  await page.goto("/");

  await uploadPayload(page, "ok.json", series(0, 61, 30, 850), "H-KEEP");
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");

  // 列表区域报错，结论不被清空
  await expect(page.getByTestId("history-error")).toBeVisible();
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");
});

test("选择恢复失败只在列表区域提示并保留当前结果", async ({ page }) => {
  await uploadPayload(page, "ok.json", series(0, 61, 30, 850), "H-KEEP2");
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");
  await expect(heatItem(page, "H-KEEP2")).toBeVisible();

  // 详情接口失败（模拟记录已被清理）；列表 GET 不受路由影响
  await page.route("**/api/history/*", (route) =>
    route.fulfill({ status: 404, json: { detail: "历史记录不存在" } }),
  );
  await heatItem(page, "H-KEEP2").click();

  await expect(page.getByTestId("history-error")).toContainText("恢复失败");
  // 当前结果保留未变
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");
});

test("服务重启后记录仍可回看", async ({ page, request }) => {
  // 该判据需要访问宿主 Docker 守护进程（verify 服务挂载 /var/run/docker.sock）；
  // 本地环境没有套接字或未以 compose 运行 api 时跳过，verify 容器内会真实重启。
  let apiContainer = "";
  try {
    apiContainer = execFileSync(
      "docker",
      ["ps", "-q", "--filter", "label=com.docker.compose.service=api"],
      { encoding: "utf8" },
    )
      .split(/\s+/)
      .filter(Boolean)[0];
  } catch {
    test.skip(true, "无法访问 Docker 守护进程，跳过服务重启判据");
  }
  test.skip(!apiContainer, "未运行 compose api 容器，跳过服务重启判据");

  await uploadPayload(page, "restart.json", series(0, 61, 30, 850), "H-RESTART");
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");
  await expect(heatItem(page, "H-RESTART")).toBeVisible();

  // 通过宿主套接字重启 api 容器（数据在命名卷的 SQLite 中，必须存活）
  execFileSync("docker", ["restart", apiContainer], { stdio: "inherit" });

  // 等待 api 经 nginx 重新就绪
  await expect
    .poll(
      async () => {
        try {
          const r = await request.get("/api/health");
          return r.status();
        } catch {
          return 0;
        }
      },
      { timeout: 60_000, intervals: [1_000] },
    )
    .toBe(200);

  // 刷新页面：记录还在，点击即可恢复当时唯一结论
  await page.reload();
  await expect(heatItem(page, "H-RESTART")).toBeVisible();
  await heatItem(page, "H-RESTART").click();
  await expect(page.getByTestId("verdict-headline")).toHaveText("保温合格");
  await expect(page.getByTestId("verdict")).toContainText("1800 秒（30 分钟）");
  await expect(page.getByTestId("verdict-source")).toContainText("restart.json");
});
