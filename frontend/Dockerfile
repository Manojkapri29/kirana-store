# Frontend image: the production build served by nginx (unprivileged, port 8080), which also forwards /api and /health to
# the backend so the browser sees ONE origin (the session cookie then needs no cross-origin settings).
#   docker build -t kirana-frontend ./frontend
# Nothing secret is built in: VITE_* values are public by design. Leave VITE_API_BASE_URL empty to use the proxy.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget -qO- http://127.0.0.1:8080/nginx-health || exit 1
