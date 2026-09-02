FROM node:22-alpine AS web-assets
ARG BOTICA_VERSION
ENV BOTICA_VERSION=${BOTICA_VERSION}
WORKDIR /build
COPY web/package.json web/package-lock.json* ./
RUN npm ci || npm install
COPY web/ ./
COPY schema/ ../schema/
RUN npm run generate:api && npm run build

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 procps \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml manage.py ./
COPY botica/ ./botica/
COPY core/ ./core/
RUN pip install --no-cache-dir .

COPY docker/entrypoint.sh /usr/local/bin/entrypoint
RUN chmod +x /usr/local/bin/entrypoint

COPY --from=web-assets /build/dist/ ./web/dist/

RUN useradd --system --uid 10001 botica && chown -R botica /app
USER botica

EXPOSE 8000
ENTRYPOINT ["entrypoint"]
CMD ["web"]
