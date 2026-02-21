#!/usr/bin/env bash
# setup-registry.sh — Start local Docker registry on host port 5050
# and configure k3s to trust it.
set -euo pipefail

REGISTRY_PORT=5050

echo "=== Starting local Docker registry on port ${REGISTRY_PORT} ==="

# Stop existing registry container if running
docker rm -f local-registry 2>/dev/null || true

# Start registry
docker run -d \
  --name local-registry \
  --restart always \
  -p "${REGISTRY_PORT}:5000" \
  -v /var/lib/registry:/var/lib/registry \
  registry:2

echo "=== Configuring k3s to trust localhost:${REGISTRY_PORT} ==="
sudo mkdir -p /etc/rancher/k3s

cat <<EOF | sudo tee /etc/rancher/k3s/registries.yaml
mirrors:
  "localhost:${REGISTRY_PORT}":
    endpoint:
      - "http://localhost:${REGISTRY_PORT}"
  "registry.builder-system.svc.cluster.local:5000":
    endpoint:
      - "http://registry.builder-system.svc.cluster.local:5000"
EOF

echo "=== Restarting k3s to pick up registry config ==="
sudo systemctl restart k3s

# After restart, k3s.yaml is reset to root-only; re-apply readable perms.
# (On fresh installs done via install-k3s.sh the --write-kubeconfig-mode flag
#  handles this automatically; here we cover the restart case.)
sleep 5
sudo chmod 0644 /etc/rancher/k3s/k3s.yaml

echo "=== Waiting for k3s to be ready again ==="
until kubectl get nodes 2>/dev/null | grep -q " Ready"; do
  echo "  waiting..."
  sleep 5
done

echo "=== Verifying registry ==="
sleep 3
curl -sf http://localhost:${REGISTRY_PORT}/v2/ && echo "Registry OK" || echo "Registry not responding yet"

echo ""
echo "=== Registry setup complete ==="
echo "Push images to: localhost:${REGISTRY_PORT}/<name>:<tag>"
echo ""
echo "Next: run scripts/bootstrap-cluster.sh"
