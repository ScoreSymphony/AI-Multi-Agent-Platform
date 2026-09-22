# syntax=docker/dockerfile:1.7

FROM caddy:2.11.4-alpine

# Remove the file capability from the upstream Caddy binary. The zero-config
# Hostinger profile grants NET_BIND_SERVICE at the container boundary only;
# the shared-Traefik profile grants no capabilities at all.
RUN apk add --no-cache libcap \
    && setcap -r /usr/bin/caddy \
    && apk del libcap

COPY deploy/docker/Caddyfile.hostinger-gateway /etc/caddy/Caddyfile.hostinger-gateway
COPY deploy/docker/Caddyfile.hostinger-setup-pending /etc/caddy/Caddyfile.hostinger-setup-pending
COPY deploy/docker/Caddyfile.hostinger-direct /etc/caddy/Caddyfile.hostinger-direct
COPY deploy/docker/Caddyfile.hostinger-direct-setup-pending /etc/caddy/Caddyfile.hostinger-direct-setup-pending
COPY deploy/docker/hostinger-gateway-entrypoint.sh /usr/local/bin/ai-map-hostinger-gateway-entrypoint

RUN chmod 0755 /usr/local/bin/ai-map-hostinger-gateway-entrypoint

ENTRYPOINT ["/usr/local/bin/ai-map-hostinger-gateway-entrypoint"]

EXPOSE 80 443 8080
