# 淬火炉保温段验收

上传温度记录 JSON，判定工件是否经历了足够长的有效保温段，避免把“短暂到温”
或“采样断档”误判为合格。工艺工程师连续验收多个炉次时，可在**最近记录**里
回看刚完成的分析，无需保留浏览器页面或重新上传原文件。

## 判定规则

上传前可在页面选择判定方式（`analysis_mode`），默认仍为**严格判定**：

### 严格判定（strict，默认）

- 有效保温段由**连续记录**构成：每条温度都落在闭区间 **840–860 °C**，且任意
  相邻记录时间差不超过 **60 秒**；一次越界或超间隔立即切段。
- 段持续时间 = 末项 `t` − 首项 `t`，达到 **1800 秒**即合格。

### 线性曲线等效保温（linear_equivalent）

边界附近缓慢升温的炉次会被严格判定的离散采样点“切段”，丢掉已经在带内的有效
时段。等效模式改为：

- 把**相邻间隔不超过 60 秒**的采样点连成线段，**带外区间**或**超限间隔**结束连续段；
  共享采样点本身越界时，两侧片段之间隔着带外时间，同样切段。
- 裁剪出每条线段温度位于闭区间 840–860 °C 的时间片，落在边界的相邻片按同一
  时刻拼接（相接点零长度，不重复计时）。
- 对片内权重 **2^((温度−850)/10)** 按时间积分；恒温片按常量积分。连续段累计
  **等效秒达到 1800 即合格**，取最早达标段，并用指数积分的**解析反函数**求首次
  达标时刻。
- 插值时刻用**整数锚点时间戳 + 十进制秒偏移**表示（`startAnchorT/startOffset`、
  `endAnchorT/endOffset`），所有浮点运算只在 0–60 秒的局部偏移上进行，避免超大
  时间戳丢精度；段快照同时给出 `equivalentSeconds`（等效秒）与 `physicalSeconds`。

两种模式下页面都只显示**唯一结论**：

- 合格 → 给出**最早达标段**的起止（线性模式为起点与首次达标时刻、累计等效秒）；
- 不合格 → 给出**最长有效段**的时长（线性模式为最长连续段等效秒）。

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
  app/soak.py         #   解析校验 + 保温段判定（strict 与线性曲线等效，纯函数）
  app/storage.py      #   SQLite 历史记录读写（成功分析落库、最近二十条、按 id 恢复）
  app/main.py         #   POST /api/analyze、GET /api/history、GET /api/history/{id}
  tests/              #   pytest：解析、区间判定、线性等效积分/反函数、临时库持久化与兼容判据
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

`POST /api/analyze`（multipart 字段 `file`，可选字段 `heat_no` 炉次号、
`analysis_mode` 判定方式）

- `analysis_mode` 取 `strict`（默认）或 `linear_equivalent`；缺省/空串/不带该
  字段的旧请求一律按严格判定，行为与旧版完全一致。
- 未知模式**先于文件解析**返回 `422`（`code: "unknown_analysis_mode"`），
  不读取落库、并由前端清除旧结论。
- `200`：strict 返回 `{ qualified, earliestQualifyingSegment, longestSegment,
  recordCount, analysisMode, ..., historyId }`；linear 的段快照为
  `{ startAnchorT, startOffset, endAnchorT, endOffset, equivalentSeconds,
  physicalSeconds, points }` —— **只有校验通过的成功分析**才写入本地 SQLite，
  并在原响应中追加 `historyId`。
- `422`：`{ detail: { code, message } }` —— 格式错误、缺字段、乱序、非有限值、
  记录数或文件大小超限、未知判定方式等，**整份拒绝且不留历史**。
- `500`：`{ detail: { code: "history_write_failed"|"history_unavailable", message } }`
  —— 持久化失败时本次分析返回明确错误，响应体不含未落库结论。

`GET /api/history` —— 按服务端分析时间倒序（同时间以 id 倒序）取最近二十条摘要：

```json
{ "items": [
  { "id": 3, "heatNo": "H-2026-001", "filename": "a.json",
    "analyzedAt": 1789000205.4, "qualified": true, "recordCount": 61,
    "analysisMode": "linear_equivalent" } ] }
```

摘要与回看结论都带 `analysisMode`；**无该字段的旧记录按严格判定**读取与展示。

`GET /api/history/{id}` —— 恢复一条记录当时的**完整结论**（用于驱动唯一结论）；
不存在返回 `404`。历史查询失败（5xx/网络问题）只在页面“最近记录”区域提示，
不影响上传流程与当前结论。重复炉次号允许存在，以适应返工复测。

### 数据库位置

容器内固定 `/app/data/history.db`（compose 以命名卷 `api-history` 挂载，
**服务重启/重建后记录仍可回看**）；本地不用 Docker 时可用环境变量
`HISTORY_DB_PATH` 指定其他位置。
