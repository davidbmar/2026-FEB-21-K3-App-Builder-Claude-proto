#!/usr/bin/env bash
# bootstrap-cluster.sh — Apply k8s bootstrap manifests and build+deploy Builder UI
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGISTRY="localhost:5050"

# Detect server IP
SERVER_IP=$(curl -sf http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null \
  || hostname -I | awk '{print $1}')
echo "Server IP: ${SERVER_IP}"

echo "=== Step 1: Create builder-system namespace ==="
kubectl apply -f "${REPO_DIR}/k8s/bootstrap/builder-namespace.yaml"

echo "=== Step 2: Apply RBAC ==="
kubectl apply -f "${REPO_DIR}/k8s/bootstrap/builder-rbac.yaml"

echo "=== Step 3: Create ANTHROPIC_API_KEY secret ==="
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "ERROR: ANTHROPIC_API_KEY environment variable is not set."
  echo "Export it before running this script:"
  echo "  export ANTHROPIC_API_KEY=sk-ant-..."
  exit 1
fi

kubectl create secret generic builder-secrets \
  --from-literal=ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
  -n builder-system \
  --dry-run=client -o yaml | kubectl apply -f -

echo "=== Step 4: Deploy in-cluster Docker registry ==="
kubectl apply -f "${REPO_DIR}/k8s/bootstrap/registry-deployment.yaml"

echo "=== Step 5: Build Builder UI image ==="
docker build -t "${REGISTRY}/builder-ui:latest" "${REPO_DIR}/builder/"
docker push "${REGISTRY}/builder-ui:latest"

echo "=== Step 6: Patch and deploy Builder UI ==="
# Substitute SERVER_IP in the ingress
sed "s/SERVER_IP_PLACEHOLDER/${SERVER_IP}/g" \
  "${REPO_DIR}/k8s/bootstrap/builder-deployment.yaml" \
  | kubectl apply -f -

echo "=== Step 7: Create host directories ==="
sudo mkdir -p /opt/builder-data /var/git/apps /var/lib/registry
sudo chown -R ubuntu:ubuntu /opt/builder-data /var/git/apps

echo "=== Waiting for Builder UI to be ready ==="
kubectl rollout status deployment/builder-ui -n builder-system --timeout=120s

echo ""
echo "=== Bootstrap complete! ==="
echo "Builder UI: http://builder.${SERVER_IP}.nip.io/"
echo ""
echo "Next: run scripts/setup-claude-cli.sh (optional)"
