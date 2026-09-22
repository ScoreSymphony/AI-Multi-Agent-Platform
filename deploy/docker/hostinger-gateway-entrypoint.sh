#!/bin/sh
set -eu

edge_mode="${AI_MAP_HOSTINGER_EDGE_MODE:-shared-traefik}"
explicit_domain="${AI_MAP_PUBLIC_DOMAIN:-}"
traefik_host="${AI_MAP_HOSTINGER_TRAEFIK_HOST:-}"
project_name="${AI_MAP_COMPOSE_PROJECT_NAME:-ai-multi-agent-platform}"

case "$edge_mode" in
  direct|shared-traefik) ;;
  *)
    echo "Invalid AI_MAP_HOSTINGER_EDGE_MODE: expected direct or shared-traefik." >&2
    exit 64
    ;;
esac

setup_pending() {
  cat >&2 <<'EOF'
AI Multi-Agent Platform Hostinger ingress is in fail-closed setup-pending mode.
The default zero-config path derives a managed Hostinger VPS hostname automatically.
If that hostname is unavailable or has been customized, set AI_MAP_PUBLIC_DOMAIN to a
public DNS hostname that points to this VPS and redeploy. Application traffic remains blocked.
EOF

  if [ "$edge_mode" = "direct" ]; then
    exec caddy run --config /etc/caddy/Caddyfile.hostinger-direct-setup-pending --adapter caddyfile
  fi

  exec caddy run --config /etc/caddy/Caddyfile.hostinger-setup-pending --adapter caddyfile
}

invalid_domain() {
  echo "Invalid Hostinger public hostname: expected a DNS hostname only (for example agents.example.com)." >&2
  echo "Do not include a scheme, path, port, wildcard, whitespace, or IP address." >&2
  exit 64
}

derive_hostinger_domain() {
  host_hostname="$(hostname 2>/dev/null || true)"
  case "$host_hostname" in
    srv*.hstgr.cloud)
      vps_id="${host_hostname#srv}"
      vps_id="${vps_id%.hstgr.cloud}"
      case "$vps_id" in
        ""|*[!0-9]*) return 1 ;;
      esac
      domain="$project_name.$host_hostname"
      domain_source="Hostinger VPS hostname"
      return 0
      ;;
  esac
  return 1
}

if [ -n "$explicit_domain" ]; then
  domain="$explicit_domain"
  domain_source="AI_MAP_PUBLIC_DOMAIN"
elif [ "$edge_mode" = "shared-traefik" ] && [ -n "$traefik_host" ]; then
  domain="$project_name.$traefik_host"
  domain_source="Hostinger TRAEFIK_HOST"
elif [ "$edge_mode" = "direct" ] && derive_hostinger_domain; then
  :
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
echo "Starting Hostinger ingress gateway for $domain (source: $domain_source, mode: $edge_mode)" >&2

if [ "$edge_mode" = "direct" ]; then
  exec caddy run --config /etc/caddy/Caddyfile.hostinger-direct --adapter caddyfile
fi

exec caddy run --config /etc/caddy/Caddyfile.hostinger-gateway --adapter caddyfile
