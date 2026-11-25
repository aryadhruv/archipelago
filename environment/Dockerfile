# syntax=docker/dockerfile:1

# APT_MIRROR must serve /debian and /debian-security; https needs CA certs in the base.
ARG BASE_IMAGE=debian:trixie-slim
FROM ${BASE_IMAGE}

ARG APT_MIRROR=""

ENV DEBIAN_FRONTEND=noninteractive

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    set -eu \
 && rm -f /etc/apt/apt.conf.d/docker-clean \
 && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache \
 && if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|http://deb.debian.org|${APT_MIRROR%/}|g" /etc/apt/sources.list.d/debian.sources || exit 1; \
    fi \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential ca-certificates chromium coreutils curl fonts-liberation git \
      libreoffice-calc libreoffice-core libsqlite3-dev netcat-openbsd nodejs npm passwd \
      poppler-utils python-is-python3 python3-pip python3.13 python3.13-dev python3.13-venv \
      socat util-linux \
 && update-ca-certificates

# Make python3 point to 3.13
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.13 1 \
 && python3 --version

# Install uv (Python package/dependency manager)
RUN python3 -m pip install --no-cache-dir --break-system-packages uv==0.13.4

# Server venvs have no dev groups; UV_NO_SYNC keeps a shell `uv run` from adding
# them. The gateway passes servers only their config `env`, so set it there too.
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_SYNC=1

WORKDIR /app

# Copy dependency files
COPY environment/pyproject.toml environment/uv.lock /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --all-groups

# Copy application code
COPY environment/ /app/
COPY mcp_servers/ /app/mcp_servers/

# Create subsystem directories
RUN mkdir -p /filesystem /.apps_data

# One RUN so the final hardlink pass dedupes files shared across venvs.
# Model-run code imports from the CODE_EXEC_MIRROR; --no-deps because pip
# re-resolving rejects the python-dotenv override in code/pyproject.toml.
ARG INSTALL_MEDICINE=false
ARG INSTALL_SCICOMP=false
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=cache,target=/root/.npm \
    set -eu \
 && for server in documents calendar mail presentations chat filesystem pdfs spreadsheets; do \
      ( cd "/app/mcp_servers/$server" && uv sync --locked --no-dev --all-extras ) || exit 1; \
    done \
 && cd /app/mcp_servers/code \
 && mkdir -p /app/lib \
 && gcc -shared -fPIC -O2 -o /app/lib/sandbox_fs.so mcp_servers/code_execution_server/sandbox_fs.c -ldl -lpthread \
 && chmod 755 /app/lib/sandbox_fs.so \
 && rm -f /usr/lib/python3*/EXTERNALLY-MANAGED \
 && ln -sf /usr/bin/pip3 /usr/local/bin/pip \
 && extra_groups="" \
 && if [ "$INSTALL_MEDICINE" = "true" ]; then extra_groups="$extra_groups --group medicine"; fi \
 && if [ "$INSTALL_SCICOMP" = "true" ]; then extra_groups="$extra_groups --group scicomp"; fi \
 && uv sync --locked --no-dev --all-extras $extra_groups \
 && uv export --frozen --no-dev --no-hashes $extra_groups > /tmp/requirements.txt \
 && grep -v '^-e ' /tmp/requirements.txt | grep -v '^mercor-rls-code-execution' | grep -v '^Resolved ' | grep -v '^pip=' > /tmp/requirements_filtered.txt \
 && uv pip install --python /usr/bin/python3 --target /usr/local/lib/code_execution --no-deps -r /tmp/requirements_filtered.txt \
 && install -D -m 755 mcp_servers/code_execution_server/sandbox_bin/soffice /usr/local/lib/code_execution/bin/soffice \
 && ( cd node_tools && npm ci --omit=dev --no-audit --no-fund ) \
 && mv node_tools/node_modules /usr/local/lib/code_execution/node_modules \
 && PYTHONPATH=/usr/local/lib/code_execution /usr/bin/python3 -c "import numpy, pandas, scipy, matplotlib, openpyxl, docx, pptx, pypdf, reportlab" \
 && rm /tmp/requirements.txt /tmp/requirements_filtered.txt \
 && hardlink --quiet --ignore-time /app/mcp_servers/*/.venv /usr/local/lib/code_execution

ENV PATH=/app/.venv/bin:$PATH \
    UV_SYSTEM_PYTHON=1 \
    PORT=8080

# Expose FastAPI port
EXPOSE 8080

CMD ["uv", "run", "uvicorn", "runner.main:app", "--host", "0.0.0.0", "--port", "8080"]
