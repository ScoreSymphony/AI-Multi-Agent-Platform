# syntax=docker/dockerfile:1.7

FROM node:22.22.2-alpine AS builder

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./

RUN npm install --global npm@11.6.0 \
    && npm ci --no-audit --no-fund

COPY frontend/ ./

RUN npm run build


FROM caddy:2.11.4-alpine AS runtime

# The official Caddy binary carries cap_net_bind_service for privileged ports.
# This profile listens on 8080 and drops every container capability, so remove the
# unused file capability to keep exec compatible with the reduced bounding set.
RUN apk add --no-cache libcap \
    && setcap -r /usr/bin/caddy \
    && apk del libcap

COPY deploy/docker/Caddyfile /etc/caddy/Caddyfile
COPY --from=builder /app/dist /srv/frontend

EXPOSE 8080
