# uv 管理依赖,与宿主本机(uv run)共用同一份 pyproject.toml + uv.lock,双模式一致
FROM python:3.13-slim

# 中文字体:run_python_code 绘图需要(否则图表中文乱码)
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装 uv:避免重复 COPY 触发重装
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uv/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

# 先复制依赖清单并安装(业务代码变动不重装)
COPY pyproject.toml uv.lock ./
COPY run.py ./
RUN uv sync --frozen --no-dev --no-install-project

# 复制业务代码
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
# Linux 默认事件循环即可(无需 Windows 的 winloop)
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
