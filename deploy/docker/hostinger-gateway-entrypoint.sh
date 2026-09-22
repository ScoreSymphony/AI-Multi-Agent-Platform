#!/bin/sh
set -eu

domain="${AI_MAP_PUBLIC_DOMAIN:-}"

setup_pending() {
  cat >&2 <<'EOF'
AI Multi-Agent Platform Hostinger ingress is in fail-closed setup-pending mode.
Set AI_MAP_PUBLIC_DOMAIN to a public DNS hostname in the Docker project environment,
point that hostname at this VPS, then redeploy the project.
Until then, the gateway returns setup guidance only and never proxies Web/API traffic.
EOF
  exec caddy run --config /etc/caddy/Caddyfile.hostinger-setup-pending --adapter caddyfile
}

invalid_domain() {
  echo "Invalid AI_MAP_PUBLIC_DOMAIN: expected a DNS hostname only (for example agents.example.com)." >&2
  echo "Do not include a scheme, path, port, wildcard, whitespace, or IP address." >&2
  exit 64
}

if [ -z "$domain" ]; then
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

echo "Starting Hostinger ingress gateway for $domain" >&2
exec caddy run --config /etc/caddy/Caddyfile.hostinger-gateway --adapter caddyfile
