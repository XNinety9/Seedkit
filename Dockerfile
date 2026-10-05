FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project

COPY README.md ./
COPY src ./src
RUN uv sync --locked

ENV SEEDKIT_DB=/data/seedkit.db PATH=/app/.venv/bin:$PATH
VOLUME /data
EXPOSE 8337
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8337/healthz')"

CMD ["seedkit", "serve"]
