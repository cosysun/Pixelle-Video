FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ARG PYTHON_PACKAGE_INDEX=https://pypi.org/simple
ARG DEBIAN_PACKAGE_MIRROR=http://deb.debian.org
ARG PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST=
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIXELLE_VIDEO_ROOT=/app \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright PATH=/app/.venv/bin:$PATH
WORKDIR /app
RUN pip install --no-cache-dir --index-url "$PYTHON_PACKAGE_INDEX" uv && \
    sed -i "s|http://deb.debian.org|$DEBIAN_PACKAGE_MIRROR|g" /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk curl && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock README.md ./
RUN uv export --quiet --frozen --no-dev --no-emit-project --no-header -o /tmp/requirements.txt && \
    uv venv && uv pip install --require-hashes --index-url "$PYTHON_PACKAGE_INDEX" -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt
RUN PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST="$PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST" .venv/bin/playwright install --with-deps --no-shell chromium
COPY pixelle_video/ pixelle_video/
COPY api/ api/
COPY templates/ templates/
COPY bgm/ bgm/
COPY config.example.yaml ./
COPY frontend/public/template-previews/ ./frontend/public/template-previews/
COPY --from=frontend /build/dist ./frontend/dist
RUN useradd --uid 10001 --create-home studio && mkdir -p /data /tmp/pixelle && chown -R studio:studio /data /tmp/pixelle && chmod -R a+rX /opt/playwright
USER studio
EXPOSE 8000
CMD ["python", "-m", "api.app", "--host", "0.0.0.0", "--port", "8000"]

FROM nginx:stable-alpine AS edge
COPY deploy/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend /build/dist /usr/share/nginx/html
