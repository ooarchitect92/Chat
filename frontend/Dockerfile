FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG VITE_API_URL=/api/v1
ARG VITE_DEMO_MODE=false
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_DEMO_MODE=$VITE_DEMO_MODE
RUN npm run build

FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10 AS runtime
ARG S3_PUBLIC_ENDPOINT_URL="http://localhost:9010"
ARG WEB_CSP_EXTRA_CONNECT_SRC="http://localhost:9010 http://127.0.0.1:9010"
COPY nginx.conf /tmp/northstar-nginx.conf.template
COPY render-nginx-config.sh /usr/local/bin/render-northstar-nginx
COPY render-nginx-config.test.sh /usr/local/bin/test-northstar-nginx-renderer
RUN chmod 0555 /usr/local/bin/render-northstar-nginx /usr/local/bin/test-northstar-nginx-renderer \
    && /usr/local/bin/test-northstar-nginx-renderer /usr/local/bin/render-northstar-nginx /tmp/northstar-nginx.conf.template \
    && /usr/local/bin/render-northstar-nginx /tmp/northstar-nginx.conf.template /etc/nginx/conf.d/default.conf \
    && rm -f /tmp/northstar-nginx.conf.template /usr/local/bin/render-northstar-nginx /usr/local/bin/test-northstar-nginx-renderer
COPY --from=build /app/dist /usr/share/nginx/html
USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
ENTRYPOINT []
CMD ["nginx", "-g", "daemon off;"]
