#!/bin/sh
set -eu

explicit_domain="${AI_MAP_PUBLIC_DOMAIN:-}"
traefik_host="${AI_MAP_HOSTINGER_TRAEFIK_HOST:-}"
project_name="${AI_MAP_COMPOSE_PROJECT_NAME:-ai-multi-agent-platform}"

setup_pending() {
  cat >&2 <<'EOF'
AI Multi-Agent Platform Hostinger ingress is in fail-closed setup-pending mode.
For Compose-from-URL deployments, add TRAEFIK_HOST (for example srv123456.hstgr.cloud)
to this Docker project's Environment variables after Hostinger Traefik is available, or
set AI_MAP_PUBLIC_DOMAIN to an explicit public DNS hostname. Until then, the gateway
returns setup guidance only and never proxies Web/API traffic.
EOF
  exec caddy run --config /etc/caddy/Caddyfile.hostinger-setup-pending --adapter caddyfile
}

invalid_domain() {
  echo "Invalid Hostinger public hostname: expected a DNS hostname only (for example agents.example.com)." >&2
  echo "Do not include a scheme, path, port, wildcard, whitespace, or IP address." >&2
  exit 64
}

if [ -n "$explicit_domain" ]; then
  domain="$explicit_domain"
  domain_source="AI_MAP_PUBLIC_DOMAIN"
elif [ -n "$traefik_host" ]; then
  domain="$project_name.$traefik_host"
  domain_source="Hostinger TRAEFIK_HOST"
else
  setup_pending
fi

case "$domain" in
  http://*|https://*|*/*|*:*|.*|*.|-*|*-|*[!A-Za-z0-9.-]*)
    invalid_domain
    ;;
esac

if [ "${#domain}" -gt 253 ]; then
  invalid_domain
fi

case "$domain" in
  *.*) ;;
  *) invalid_domain ;;
esac

case "$domain" in
  *[!0-9.]* ) ;;
  *) invalid_domain ;;
esac

old_ifs="$IFS"
IFS=.
set -- $domain
IFS="$old_ifs"

for label in "$@"; do
  if [ -z "$label" ] || [ "${#label}" -gt 63 ]; then
    invalid_domain
  fi
  case "$label" in
    -*|*-) invalid_domain ;;
  esac
done

export AI_MAP_PUBLIC_DOMAIN="$domain"
echo "Starting Hostinger ingress gateway for $domain (source: $domain_source)" >&2
exec caddy run --config /etc/caddy/Caddyfile.hostinger-gateway --adapter caddyfile
