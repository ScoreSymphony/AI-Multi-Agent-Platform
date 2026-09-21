# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip wheel --wheel-dir /wheels ".[server]"


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --system --gid 10001 ai-map \
    && useradd --system --uid 10001 --gid ai-map \
       --home-dir /var/lib/ai-multi-agent-platform \
       --shell /usr/sbin/nologin ai-map \
    && mkdir -p /var/lib/ai-multi-agent-platform \
    && chown -R ai-map:ai-map /var/lib/ai-multi-agent-platform

COPY --from=builder /wheels /wheels

RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels \
      "ai-multi-agent-platform[server]==0.0.1" \
    && rm -rf /wheels

WORKDIR /var/lib/ai-multi-agent-platform

USER ai-map

EXPOSE 8000

CMD ["platform-server", "serve"]
