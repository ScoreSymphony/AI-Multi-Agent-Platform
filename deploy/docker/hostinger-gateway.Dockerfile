# syntax=docker/dockerfile:1.7

FROM caddy:2.11.4-alpine

# The official Caddy binary carries cap_net_bind_service for privileged ports.
# This gateway listens only on 8080 and drops every container capability.
RUN apk add --no-cache libcap \
    && setcap -r /usr/bin/caddy \
    && apk del libcap

COPY deploy/docker/Caddyfile.hostinger-gateway /etc/caddy/Caddyfile.hostinger-gateway
COPY deploy/docker/Caddyfile.hostinger-setup-pending /etc/caddy/Caddyfile.hostinger-setup-pending
COPY deploy/docker/hostinger-gateway-entrypoint.sh /usr/local/bin/ai-map-hostinger-gateway-entrypoint

RUN chmod 0755 /usr/local/bin/ai-map-hostinger-gateway-entrypoint

ENTRYPOINT ["/usr/local/bin/ai-map-hostinger-gateway-entrypoint"]

EXPOSE 8080
