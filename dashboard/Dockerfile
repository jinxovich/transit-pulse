FROM node:22-alpine AS build
WORKDIR /repo/dashboard
COPY dashboard/package*.json ./
RUN npm ci
COPY contracts/ts /repo/contracts/ts
COPY dashboard/ ./
RUN npm run build

FROM nginx:1.27-alpine
ENV BACKEND_UPSTREAM=backend:8000
COPY dashboard/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /repo/dashboard/dist /usr/share/nginx/html
EXPOSE 8080
