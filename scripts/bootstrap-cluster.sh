#!/usr/bin/env bash
# bootstrap-cluster.sh — Apply k8s bootstrap manifests and build+deploy Builder UI
# Run from the project root: ./scripts/bootstrap-cluster.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGISTRY="localhost:5050"

# Detect server IP (EC2 metadata → fallback to primary IP)
SERVER_IP=$(curl -sf --connect-timeout 3 http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null \
  || hostname -I | awk '{print $1}')
echo "Server IP: ${SERVER_IP}"
echo "Builder URL will be: http://builder.${SERVER_IP}.nip.io/"
echo ""

# Verify kubectl is working
kubectl get nodes || {
  echo "ERROR: kubectl not configured. Run scripts/install-k3s.sh first."
  exit 1
}

# Verify ANTHROPIC_API_KEY
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "ERROR: ANTHROPIC_API_KEY is not set."
  echo "Export it first:"
  echo "  export ANTHROPIC_API_KEY=sk-ant-..."
  exit 1
fi

echo "=== Step 1: Create builder-system namespace ==="
kubectl apply -f "${REPO_DIR}/k8s/bootstrap/builder-namespace.yaml"

echo "=== Step 2: Apply RBAC ==="
kubectl apply -f "${REPO_DIR}/k8s/bootstrap/builder-rbac.yaml"

echo "=== Step 3: Create ANTHROPIC_API_KEY secret ==="
kubectl create secret generic builder-secrets \
  --from-literal=ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
  -n builder-system \
  --dry-run=client -o yaml | kubectl apply -f -

echo "=== Step 4: Deploy in-cluster Docker registry ==="
sudo mkdir -p /var/lib/registry
kubectl apply -f "${REPO_DIR}/k8s/bootstrap/registry-deployment.yaml"
kubectl rollout status deployment/docker-registry -n builder-system --timeout=60s

echo "=== Step 5: Create host data directories ==="
sudo mkdir -p /opt/builder-data /var/git/apps
# Ensure ubuntu user can write to these
sudo chown -R ubuntu:ubuntu /opt/builder-data /var/git/apps 2>/dev/null || true

echo "=== Step 6: Build and push Builder UI image ==="
docker build -t "${REGISTRY}/builder-ui:latest" "${REPO_DIR}/builder/"
docker push "${REGISTRY}/builder-ui:latest"

echo "=== Step 7: Deploy Builder UI ==="
sed "s/SERVER_IP_PLACEHOLDER/${SERVER_IP}/g" \
  "${REPO_DIR}/k8s/bootstrap/builder-deployment.yaml" \
  | kubectl apply -f -

echo "=== Step 8: Wait for Builder UI to be ready ==="
kubectl rollout status deployment/builder-ui -n builder-system --timeout=120s

echo ""
echo "========================================"
echo "  Bootstrap complete!"
echo "========================================"
echo ""
echo "  Builder UI: http://builder.${SERVER_IP}.nip.io/"
echo "  Registry:   localhost:5050"
echo ""
kubectl get pods -n builder-system
echo ""
echo "Next step: open the Builder UI in your browser."
