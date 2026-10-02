# syntax=docker/dockerfile:1.7
FROM node:20-alpine AS base

# Install dependencies only when needed
FROM base AS deps
RUN apk add --no-cache libc6-compat
WORKDIR /app

COPY package.json package-lock.json* ./
RUN --mount=type=cache,target=/root/.npm,id=npm-cache,sharing=locked \
    npm ci --prefer-offline --no-audit --no-fund

# Rebuild the source code only when needed
FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN mkdir -p /app/public

ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_WS_URL
ARG NEXT_PUBLIC_BIMA_BASE_URL
ARG NEXT_PUBLIC_BIMA_CLIENT_ID=
ARG NEXT_PUBLIC_BIMA_REDIRECT_URI=
ARG NEXT_PUBLIC_LOCAL_DEV_AUTH_BYPASS=false
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL
ENV NEXT_PUBLIC_BIMA_BASE_URL=$NEXT_PUBLIC_BIMA_BASE_URL
ENV NEXT_PUBLIC_BIMA_CLIENT_ID=$NEXT_PUBLIC_BIMA_CLIENT_ID
ENV NEXT_PUBLIC_BIMA_REDIRECT_URI=$NEXT_PUBLIC_BIMA_REDIRECT_URI
ENV NEXT_PUBLIC_LOCAL_DEV_AUTH_BYPASS=$NEXT_PUBLIC_LOCAL_DEV_AUTH_BYPASS
ENV NEXT_TELEMETRY_DISABLED=1
RUN --mount=type=cache,target=/app/.next/cache,id=nextjs-cache,sharing=locked \
    npm run build

# Production image, copy all the files and run next
FROM base AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public

# Automatically leverage output traces to reduce image size
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]
