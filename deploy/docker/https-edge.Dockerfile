# syntax=docker/dockerfile:1.7

FROM caddy:2.11.4-alpine

COPY deploy/docker/Caddyfile.public-https /etc/caddy/Caddyfile

EXPOSE 80 443
