# syntax=docker/dockerfile:1.7

FROM caddy:2.11.4-alpine

COPY deploy/docker/Caddyfile.public-https /etc/caddy/Caddyfile
COPY deploy/docker/Caddyfile.setup-pending /etc/caddy/Caddyfile.setup-pending
COPY deploy/docker/https-edge-entrypoint.sh /usr/local/bin/ai-map-https-edge-entrypoint

RUN chmod 0755 /usr/local/bin/ai-map-https-edge-entrypoint

ENTRYPOINT ["/usr/local/bin/ai-map-https-edge-entrypoint"]

EXPOSE 80 443
