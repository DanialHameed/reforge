#!/usr/bin/env bash
set -euo pipefail

# ReForge server setup (Ubuntu 22.04+ recommended)
# - Installs Docker + Compose plugin
# - Creates app directory
# - Leaves deployment to GitHub Actions (or manual compose)

APP_DIR="/opt/reforge"

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)."
  exit 1
fi

apt-get update -y
apt-get install -y ca-certificates curl gnupg lsb-release

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

mkdir -p "$APP_DIR"
chown -R "$SUDO_USER:$SUDO_USER" "$APP_DIR" || true

echo "Done."
echo "Next steps:"
echo "- Put your repo in $APP_DIR (or configure your CI to deploy there)"
echo "- Create $APP_DIR/.env from the repo's .env.example"
echo "- Run: docker compose -f docker-compose.prod.yml up -d --build"
