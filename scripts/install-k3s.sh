#!/usr/bin/env bash
# install-k3s.sh — Install k3s with Traefik on ports 80/443
# Safe to re-run: skips install if k3s is already running.
set -euo pipefail

echo "=== Installing k3s ==="
# --write-kubeconfig-mode=0644 makes /etc/rancher/k3s/k3s.yaml readable
# by all users so kubectl works without sudo.
curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--write-kubeconfig-mode=0644" sh -

echo "=== Setting up kubeconfig for ubuntu user ==="
mkdir -p /home/ubuntu/.kube
cp /etc/rancher/k3s/k3s.yaml /home/ubuntu/.kube/config
chmod 600 /home/ubuntu/.kube/config

# Persist KUBECONFIG so every new shell finds it
if ! grep -q "KUBECONFIG" /home/ubuntu/.bashrc 2>/dev/null; then
  echo 'export KUBECONFIG=/home/ubuntu/.kube/config' >> /home/ubuntu/.bashrc
fi
export KUBECONFIG=/home/ubuntu/.kube/config

echo "=== Waiting for k3s node to be Ready ==="
until kubectl get nodes 2>/dev/null | grep -q " Ready"; do
  echo "  waiting..."
  sleep 5
done

echo "=== Verifying installation ==="
kubectl get nodes
kubectl get pods -n kube-system

SERVER_IP=$(curl -sf http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null \
  || hostname -I | awk '{print $1}')
echo ""
echo "=== k3s installed! ==="
echo "Node:       $(kubectl get nodes --no-headers | awk '{print $1, $2}')"
echo "Server IP:  ${SERVER_IP}"
echo "Builder URL (after full setup): http://builder.${SERVER_IP}.nip.io/"
echo ""
echo "Next: run scripts/setup-registry.sh"
