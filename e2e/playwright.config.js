import { defineConfig } from "@playwright/test";

// 默认打本机 compose 映射出的 WEB_PORT；verify 服务内用 WEB_BASE_URL=http://web
const baseURL =
  process.env.WEB_BASE_URL ?? `http://localhost:${process.env.WEB_PORT ?? 8080}`;

// verify 容器内以 root 运行并挂载 Docker 套接字做真实重启；
// root 启动 Chromium 必须关闭 setuid sandbox（一次性验收容器，可接受）
const launchOptions =
  typeof process.getuid === "function" && process.getuid() === 0
    ? { args: ["--no-sandbox", "--disable-setuid-sandbox"] }
    : {};

export default defineConfig({
  testDir: ".",
  timeout: 30_000,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL,
    screenshot: "only-on-failure",
    launchOptions,
  },
});
