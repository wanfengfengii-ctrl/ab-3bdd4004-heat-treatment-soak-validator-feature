# 淬火炉保温段验收

上传温度记录 JSON，判定工件是否经历了足够长的有效保温段，避免把“短暂到温”
或“采样断档”误判为合格。工艺工程师连续验收多个炉次时，可在**最近记录**里
回看刚完成的分析，无需保留浏览器页面或重新上传原文件。

## 判定规则

- 有效保温段由**连续记录**构成：每条温度都落在闭区间 **840–860 °C**，且任意
  相邻记录时间差不超过 **60 秒**；一次越界或超间隔立即切段。
- 段持续时间 = 末项 `t` − 首项 `t`，达到 **1800 秒**即合格。
- 页面只显示**唯一结论**：
  - 合格 → 给出**最早达标段**的起止时间；
  - 不合格 → 给出**最长有效段**的时长。

## 上传文件要求（任一不满足即整份拒绝并清除旧结果）

- JSON 文件，根必须是数组；每项含整数秒时间戳 `t` 与摄氏温度 `temp`。
- 记录数 **2–10000**；时间戳**严格递增**；温度必须为**有限数值**
  （拒绝 `NaN` / `Infinity` / 溢出值）。
- 文件大小上限 **2 MiB**。

示例（合格）：

```json
[{"t": 0, "temp": 850}, {"t": 30, "temp": 851}, {"t": 60, "temp": 849}]
```

## 文件结构

```
compose.yaml          # 一键启动：api + web + verify（api 的 SQLite 在命名卷 api-history）
api/                  # FastAPI 后端
  app/soak.py         #   解析校验 + 保温段判定（纯函数）
  app/storage.py      #   SQLite 历史记录读写（成功分析落库、最近二十条、按 id 恢复）
  app/main.py         #   POST /api/analyze、GET /api/history、GET /api/history/{id}
  tests/              #   pytest：解析、区间判定、临时库持久化与兼容判据
web/                  # React (Vite) 前端
  src/lib/verdict.js  #   分析结果 → 唯一结论视图模型
  src/lib/history.js  #   最近记录摘要 → 列表视图模型
  src/HistoryPanel.jsx#   最近记录侧栏（空态/错误只留在列表区域）
  src/lib/*.test.js   #   Vitest：结论判定与列表映射判据
  nginx.conf          #   生产环境 /api 反代到内部 api 服务
e2e/                  # Playwright：浏览器真实上传链路判据（含经 Docker 套接字重启 api）
verify/               # 一次性验收服务（pytest → vitest → playwright）
```

## 运行

```bash
# 启动应用（默认 http://localhost:8080，可用 WEB_PORT 覆盖宿主端口）
WEB_PORT=9000 docker compose up --build api web

# 一次性验收：构建并运行 verify，全部判据通过后容器退出
docker compose up --build --exit-code-from verify verify
```

`api` 不发布宿主端口，浏览器只访问 `web`，由 nginx 将 `/api/*` 代理到内部
`api:8000`。

## 本地开发（不用 Docker）

```bash
# 后端：http://localhost:8000
cd api && pip install -r requirements-dev.txt
uvicorn app.main:app --reload          # 测试：python -m pytest

# 前端：http://localhost:5173（/api 已代理到 8000）
cd web && npm ci
npm run dev                            # 测试：npm test

# 端到端（需 web 与 api 均在运行）
cd e2e && npm ci && npx playwright install chromium
WEB_PORT=8080 npx playwright test
# “服务重启后记录仍可回看”需可访问 Docker 守护进程（compose verify 已挂载
# /var/run/docker.sock）；本地无套接字或未以 compose 运行 api 时该用例自动跳过。
```

## API

`POST /api/analyze`（multipart 字段 `file`，可选字段 `heat_no` 炉次号）

- `200`：`{ qualified, earliestQualifyingSegment, longestSegment, recordCount, ..., historyId }`
  —— **只有校验通过的成功分析**才写入本地 SQLite，并在原响应中追加记录标识
  `historyId`；不带 `heat_no` 的旧客户端请求行为完全不变。
- `422`：`{ detail: { code, message } }` —— 格式错误、缺字段、乱序、非有限值、
  记录数或文件大小超限等，**整份拒绝且不留历史**。
- `500`：`{ detail: { code: "history_write_failed"|"history_unavailable", message } }`
  —— 持久化失败时本次分析返回明确错误，响应体不含未落库结论。

`GET /api/history` —— 按服务端分析时间倒序（同时间以 id 倒序）取最近二十条摘要：

```json
{ "items": [
  { "id": 3, "heatNo": "H-2026-001", "filename": "a.json",
    "analyzedAt": 1789000205.4, "qualified": true, "recordCount": 61 } ] }
```

`GET /api/history/{id}` —— 恢复一条记录当时的**完整结论**（用于驱动唯一结论）；
不存在返回 `404`。历史查询失败（5xx/网络问题）只在页面“最近记录”区域提示，
不影响上传流程与当前结论。重复炉次号允许存在，以适应返工复测。

### 数据库位置

容器内固定 `/app/data/history.db`（compose 以命名卷 `api-history` 挂载，
**服务重启/重建后记录仍可回看**）；本地不用 Docker 时可用环境变量
`HISTORY_DB_PATH` 指定其他位置。
