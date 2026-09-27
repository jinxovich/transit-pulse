FROM node:22-alpine AS build
WORKDIR /repo/dashboard
COPY dashboard/package*.json ./
# Медленная сеть или недоступен registry.npmjs.org:
#   docker compose build --build-arg NPM_REGISTRY=https://registry.npmmirror.com dashboard
ARG NPM_REGISTRY=https://registry.npmjs.org/
RUN npm ci --registry="$NPM_REGISTRY" --fetch-retries=5 --fetch-timeout=120000
COPY contracts/ts /repo/contracts/ts
COPY dashboard/ ./
RUN npm run build

FROM nginx:1.27-alpine
ENV BACKEND_UPSTREAM=backend:8000
COPY dashboard/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /repo/dashboard/dist /usr/share/nginx/html
EXPOSE 8080
