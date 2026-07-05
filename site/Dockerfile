# Astro SSR standalone server for Cloud Run (SPEC §6).
# Build context is the repo root: docker build -f site/Dockerfile .

FROM node:22-slim AS build
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY site/package.json site/package.json
RUN pnpm install --frozen-lockfile
COPY site site
# PUBLIC_* vars are inlined at build time; pass via --build-arg in CI.
ARG PUBLIC_GA_ID=""
ARG PUBLIC_ADS_ENABLED="false"
ARG PUBLIC_ADSENSE_CLIENT=""
ARG PUBLIC_AMAZON_TAG=""
ARG SITE_URL="https://recalllookup.example"
ENV PUBLIC_GA_ID=$PUBLIC_GA_ID \
    PUBLIC_ADS_ENABLED=$PUBLIC_ADS_ENABLED \
    PUBLIC_ADSENSE_CLIENT=$PUBLIC_ADSENSE_CLIENT \
    PUBLIC_AMAZON_TAG=$PUBLIC_AMAZON_TAG \
    SITE_URL=$SITE_URL
RUN pnpm --filter site build

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production HOST=0.0.0.0 PORT=8080
COPY --from=build /app/site/dist ./dist
EXPOSE 8080
USER node
CMD ["node", "./dist/server/entry.mjs"]
