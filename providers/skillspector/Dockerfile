FROM python:3.12-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b AS builder

WORKDIR /app
COPY providers/skillspector/requirements.lock /tmp/requirements.lock
RUN python -m venv .venv \
    && .venv/bin/pip install --no-cache-dir -r /tmp/requirements.lock

COPY .candidate/skillspector/pyproject.toml .candidate/skillspector/README.md ./
COPY .candidate/skillspector/src/ src/
RUN .venv/bin/pip install --no-cache-dir --no-deps .

FROM python:3.12-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b

COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH"
LABEL org.opencontainers.image.source="https://github.com/NVIDIA/SkillSpector" \
      org.opencontainers.image.revision="69dcdfb74487d361ba4c811d088cfdea2ff3a9dc" \
      io.scoresymphony.skillspector.dependency-set-sha256="d6716d890040ac73494a8422ea51ef11229e71e0c0d9b538a95316a3a644fc61" \
      io.scoresymphony.skillspector.scan-mode="static_no_llm_network_none" \
      io.scoresymphony.skillspector.policy-config-revision="skillspector-static-v1"

WORKDIR /scan
ENTRYPOINT ["skillspector"]
