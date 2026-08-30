# syntax=docker/dockerfile:1

# ---- dependencies ------------------------------------------------------------
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

# ---- build -------------------------------------------------------------------
FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Baked in at build time because Next inlines NEXT_PUBLIC_* into the client bundle.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---- runtime -----------------------------------------------------------------
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000

RUN addgroup -g 10001 -S nodejs && adduser -S nextjs -u 10001

# `output: 'standalone'` in next.config.mjs produces a self-contained server with
# only the modules actually imported, which keeps this image small.
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
HEALTHCHECK --interval=20s --timeout=5s --start-period=20s --retries=5 \
    CMD wget -qO- http://localhost:3000 >/dev/null || exit 1
CMD ["node", "server.js"]
