#!/usr/bin/env bash
# install-k3s.sh — Install k3s with Traefik on ports 80/443
set -euo pipefail

echo "=== Installing k3s ==="
curl -sfL https://get.k3s.io | sh -

echo "=== Waiting for k3s to be ready ==="
until kubectl get nodes 2>/dev/null | grep -q " Ready"; do
  echo "Waiting for node to be Ready..."
  sleep 5
done

echo "=== Setting up kubeconfig for ubuntu user ==="
mkdir -p /home/ubuntu/.kube
sudo cp /etc/rancher/k3s/k3s.yaml /home/ubuntu/.kube/config
sudo chown ubuntu:ubuntu /home/ubuntu/.kube/config
chmod 600 /home/ubuntu/.kube/config

echo "=== Verifying installation ==="
kubectl get nodes
kubectl get pods -n kube-system

SERVER_IP=$(curl -sf http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null \
  || hostname -I | awk '{print $1}')
echo ""
echo "=== k3s installed! ==="
echo "Server IP: ${SERVER_IP}"
echo "Builder will be at: http://builder.${SERVER_IP}.nip.io/"
echo ""
echo "Next: run scripts/setup-registry.sh"
