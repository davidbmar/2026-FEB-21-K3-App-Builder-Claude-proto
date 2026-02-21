#!/usr/bin/env bash
# setup-claude-cli.sh — Install Claude CLI + ttyd web terminal
set -euo pipefail

echo "=== Installing Node.js (for Claude CLI) ==="
if ! command -v node &>/dev/null; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi
node --version

echo "=== Installing Claude CLI globally ==="
sudo npm install -g @anthropic-ai/claude-code
claude --version || echo "Note: claude CLI installed, verify with: claude --version"

echo "=== Installing tmux (for interactive sessions) ==="
sudo apt-get install -y tmux

echo "=== Installing ttyd (web terminal) ==="
if ! command -v ttyd &>/dev/null; then
  # Download latest ttyd binary
  TTYD_VERSION=$(curl -sf https://api.github.com/repos/tsl0922/ttyd/releases/latest \
    | grep '"tag_name"' | cut -d'"' -f4)
  curl -sfL "https://github.com/tsl0922/ttyd/releases/download/${TTYD_VERSION}/ttyd.x86_64" \
    -o /tmp/ttyd
  sudo install -m 755 /tmp/ttyd /usr/local/bin/ttyd
  echo "ttyd installed: $(ttyd --version)"
fi

echo "=== Creating host directories for workspaces ==="
sudo mkdir -p /var/git/apps
sudo chown -R ubuntu:ubuntu /var/git/apps

echo ""
echo "=== Claude CLI setup complete ==="
echo "Usage: the Builder UI will launch claude sessions automatically."
echo "Manual: tmux attach -t claude-<appname>"
