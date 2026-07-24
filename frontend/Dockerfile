# Build stage
FROM m.daocloud.io/docker.io/library/node:22.12.0-slim AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm config set registry https://registry.npmmirror.com && npm ci

COPY . .

# Vite env vars are embedded at build time
ARG VITE_SENTRY_DSN=""
ENV VITE_SENTRY_DSN=$VITE_SENTRY_DSN

RUN npm run build

# Production stage
FROM m.daocloud.io/docker.io/library/nginx:1.27.3-alpine3.20

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Note: nginx:alpine runs master as root and workers as nginx by default.
# We keep this behavior to avoid permission issues with pid files and port 80.
RUN chown -R nginx:nginx /usr/share/nginx/html /etc/nginx/conf.d /var/cache/nginx

EXPOSE 80

# Health check — use wget compatible with BusyBox/Alpine
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://localhost/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
