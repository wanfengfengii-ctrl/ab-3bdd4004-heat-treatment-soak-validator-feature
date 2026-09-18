#!/usr/bin/env bash
# 一次性验收：pytest（解析）→ vitest（区间/结论判定）→ playwright（浏览器上传）
set -euo pipefail

echo "==> [1/3] pytest：文件解析与保温段判定"
(cd /work/api && python3 -m pytest -q)

echo "==> [2/3] vitest：前端结论视图判定"
(cd /work/web && npx vitest run)

echo "==> 等待 web 服务就绪 (${WEB_BASE_URL})"
ready=0
for _ in $(seq 1 60); do
  if curl -fsS "${WEB_BASE_URL}/" > /dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [ "$ready" -ne 1 ]; then
  echo "web 服务未在限定时间内就绪" >&2
  exit 1
fi

echo "==> [3/3] playwright：浏览器真实上传链路"
(cd /work/e2e && npx playwright test)

echo "ALL CHECKS PASSED"
