#!/bin/sh
set -eu

edge_mode="${AI_MAP_HOSTINGER_EDGE_MODE:-shared-traefik}"
explicit_domain="${AI_MAP_PUBLIC_DOMAIN:-}"
traefik_host="${AI_MAP_HOSTINGER_TRAEFIK_HOST:-}"
project_name="${AI_MAP_COMPOSE_PROJECT_NAME:-ai-multi-agent-platform}"

case "$edge_mode" in
  direct|shared-traefik|traefik-passthrough) ;;
  *)
    echo "Invalid AI_MAP_HOSTINGER_EDGE_MODE: expected direct, shared-traefik, or traefik-passthrough." >&2
    exit 64
    ;;
esac

setup_pending() {
  cat >&2 <<'EOF'
AI Multi-Agent Platform Hostinger ingress is in fail-closed setup-pending mode.
For the managed hPanel Open path, set TRAEFIK_HOST to this VPS hostname as shown in hPanel
(for example srv123456.hstgr.cloud). For a custom public DNS name, use the explicit custom-domain
profile with AI_MAP_PUBLIC_DOMAIN. Application traffic remains blocked until the hostname contract
is valid.
EOF

  if [ "$edge_mode" = "direct" ] || [ "$edge_mode" = "traefik-passthrough" ]; then
    exec caddy run --config /etc/caddy/Caddyfile.hostinger-direct-setup-pending --adapter caddyfile
  fi

  exec caddy run --config /etc/caddy/Caddyfile.hostinger-setup-pending --adapter caddyfile
}

invalid_domain() {
  echo "Invalid Hostinger public hostname: expected a DNS hostname only (for example agents.example.com)." >&2
  echo "Do not include a scheme, path, port, wildcard, whitespace, or IP address." >&2
  exit 64
}

normalize_hostinger_hostname() {
  candidate="$1"
  case "$candidate" in
    srv*.hstgr.cloud)
      vps_id="${candidate#srv}"
      vps_id="${vps_id%.hstgr.cloud}"
      normalized_hostname="$candidate"
      ;;
    srv*)
      vps_id="${candidate#srv}"
      normalized_hostname="$candidate.hstgr.cloud"
      ;;
    *)
      return 1
      ;;
  esac

  case "$vps_id" in
    ""|*[!0-9]*) return 1 ;;
  esac

  printf "%s\n" "$normalized_hostname"
}

derive_hostinger_domain() {
  host_hostname="$(hostname 2>/dev/null || true)"
  managed_hostname="$(normalize_hostinger_hostname "$host_hostname")" || return 1
  domain="$project_name.$managed_hostname"
  domain_source="Hostinger VPS hostname"
  return 0
}

verify_traefik_host_matches_runtime() {
  case "$traefik_host" in
    srv*.hstgr.cloud) ;;
    *)
      echo "Invalid Hostinger TRAEFIK_HOST: managed hPanel Open requires the full srv<digits>.hstgr.cloud hostname." >&2
      return 1
      ;;
  esac

  supplied_hostname="$(normalize_hostinger_hostname "$traefik_host")" || {
    echo "Invalid Hostinger TRAEFIK_HOST: managed hPanel Open requires the full srv<digits>.hstgr.cloud hostname." >&2
    return 1
  }

  runtime_hostname="$(hostname 2>/dev/null || true)"
  runtime_hostname="$(normalize_hostinger_hostname "$runtime_hostname")" || {
    echo "Runtime host UTS hostname is not a validated Hostinger hostname." >&2
    return 1
  }

  if [ "$supplied_hostname" != "$runtime_hostname" ]; then
    echo "TRAEFIK_HOST ($supplied_hostname) does not match runtime host UTS hostname ($runtime_hostname)." >&2
    return 1
  fi

  domain="$project_name.$runtime_hostname"
  domain_source="verified Hostinger TRAEFIK_HOST"
  return 0
}

if [ -n "$explicit_domain" ]; then
  domain="$explicit_domain"
  domain_source="AI_MAP_PUBLIC_DOMAIN"
elif [ "$edge_mode" = "shared-traefik" ] && [ -n "$traefik_host" ]; then
  domain="$project_name.$traefik_host"
  domain_source="Hostinger TRAEFIK_HOST"
elif [ "$edge_mode" = "traefik-passthrough" ] && [ -n "$traefik_host" ]; then
  verify_traefik_host_matches_runtime || setup_pending
elif { [ "$edge_mode" = "direct" ] || [ "$edge_mode" = "traefik-passthrough" ]; }   && derive_hostinger_domain; then
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

if [ "$edge_mode" = "direct" ] || [ "$edge_mode" = "traefik-passthrough" ]; then
  exec caddy run --config /etc/caddy/Caddyfile.hostinger-direct --adapter caddyfile
fi

exec caddy run --config /etc/caddy/Caddyfile.hostinger-gateway --adapter caddyfile
