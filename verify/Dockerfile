FROM node:22-bookworm

# Python（跑 pytest）、docker CLI（端到端经宿主套接字重启 api）与基础工具
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 python3-pip curl ca-certificates docker.io \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work

# 后端依赖（含测试依赖），源码在 run.sh 运行前由 COPY . . 提供
COPY api/requirements.txt api/requirements-dev.txt ./api/
RUN pip install --no-cache-dir --break-system-packages -r api/requirements-dev.txt

# 前端依赖
COPY web/package.json web/package-lock.json ./web/
RUN cd web && npm ci

# e2e 依赖与 Playwright 浏览器（含系统依赖）
COPY e2e/package.json e2e/package-lock.json ./e2e/
RUN cd e2e && npm ci && npx playwright install --with-deps chromium

# 全部源码与测试
COPY . .

ENV WEB_BASE_URL=http://web
CMD ["bash", "verify/run.sh"]
