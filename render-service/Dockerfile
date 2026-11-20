# Base images are pinned by digest (@sha256): the tag is only a label.
# To update one: docker buildx imagetools inspect <tag> and paste the new digest.
FROM node:24-bookworm-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS renderer-system

# Keep completed packages available across interrupted builds.
RUN --mount=type=cache,id=openshorts-renderer-apt,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=openshorts-renderer-apt-lists,target=/var/lib/apt/lists,sharing=locked \
    if [ -f /etc/apt/apt.conf.d/docker-clean ]; then \
      mv /etc/apt/apt.conf.d/docker-clean /tmp/docker-clean; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends chromium ffmpeg \
    && if [ -f /tmp/docker-clean ]; then \
      mv /tmp/docker-clean /etc/apt/apt.conf.d/docker-clean; \
    fi

FROM renderer-system

ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
ENV REMOTION_BUNDLE_PATH=/app/remotion
ENV OUTPUT_DIR=/output

WORKDIR /app

# Copy render service source
COPY render-service/package.json render-service/package-lock.json ./
RUN --mount=type=cache,id=openshorts-renderer-npm,target=/root/.npm,sharing=locked \
    npm ci --no-audit --no-fund --include=optional --prefer-offline --fetch-retries=6 \
    && node -e "require.resolve('@remotion/compositor-linux-' + process.arch + '-gnu')"

COPY render-service/tsconfig.json ./
COPY render-service/src/ ./src/

# Copy remotion source for bundling
COPY remotion/package.json remotion/package-lock.json /app/remotion/
COPY remotion/tsconfig.json /app/remotion/tsconfig.json
COPY remotion/src/ /app/remotion/src/
COPY remotion/public/ /app/remotion/public/

# Install remotion deps
RUN --mount=type=cache,id=openshorts-renderer-npm,target=/root/.npm,sharing=locked \
    cd /app/remotion && npm ci --no-audit --no-fund --prefer-offline --fetch-retries=6

# Build TypeScript
RUN npm run build

EXPOSE 3100
CMD ["node", "dist/server.js"]
