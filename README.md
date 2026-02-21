# k3s App Builder Platform

**Describe → Preview → Publish** — A self-contained platform for deploying apps with plain language + Claude, entirely within k3s.

Project: `2026-FEB-21-K3-App-Builder-Claude-proto`

---

## Architecture

```
Internet → k3s Traefik (ports 80/443)
              ├── builder.IP.nip.io       → Builder UI
              ├── appname.IP.nip.io       → User app (prod)
              └── appname-preview.IP.nip.io → User app (preview)

k3s cluster (single node)
  ├── namespace: builder-system
  │     ├── Builder UI (FastAPI + HTMX)
  │     ├── Local Docker Registry (:5000)
  │     └── builder-controller ServiceAccount
  └── namespace: app-<name>   (one per user app)
        ├── Deployment: <name>-preview
        ├── Deployment: <name>-prod
        ├── ResourceQuota
        └── NetworkPolicy (deny cross-namespace)
```

---

## Quick Start (Steps 0–4)

### Prerequisites
- Ubuntu 24.04 EC2 (7.6 GB+ RAM, 84 GB disk)
- Docker installed
- `ANTHROPIC_API_KEY` available

### Step 1: Install k3s
```bash
chmod +x scripts/*.sh
./scripts/install-k3s.sh
```

### Step 2: Set up local Docker registry
```bash
./scripts/setup-registry.sh
```

### Step 3: Bootstrap the cluster
```bash
export ANTHROPIC_API_KEY=sk-ant-...
./scripts/bootstrap-cluster.sh
```
This builds and deploys the Builder UI. Visit `http://builder.YOUR_IP.nip.io/`

### Step 4: (Optional) Install Claude CLI + ttyd
```bash
./scripts/setup-claude-cli.sh
```

---

## Usage

1. Open `http://builder.YOUR_IP.nip.io/`
2. Click **+ New App**, choose a template and enter an app name
3. On the app detail page, describe what you want Claude to build
4. Click **Generate** — Claude streams the code
5. Click **Build & Deploy Preview** — docker build runs, pod deploys
6. Visit the preview URL to test your app
7. Click **Publish to Production** to promote
8. Use **Rollback** if something breaks

---

## Project Structure

```
├── builder/              # Builder UI (FastAPI + Jinja2 + HTMX)
│   ├── main.py           # All routes
│   ├── k8s_ops.py        # kubectl wrapper
│   ├── claude_ops.py     # Claude API streaming
│   ├── build_ops.py      # docker build + git ops
│   ├── templates/        # Jinja2 HTML templates
│   ├── static/           # CSS
│   └── claude_prompts/   # System prompts per template
├── k8s/
│   ├── bootstrap/        # One-time setup manifests
│   └── templates/        # Jinja2 k8s YAML templates
├── app_templates/        # Scaffolding per app type
│   ├── simple-api/
│   ├── static-site/
│   ├── webhook/
│   └── scheduled-job/
└── scripts/              # Setup + operational scripts
```

---

## App Templates

| Template | Description |
|---|---|
| `simple-api` | FastAPI Python REST API |
| `static-site` | HTML/CSS/JS served by nginx |
| `webhook` | FastAPI with HMAC-SHA256 signature verification |
| `scheduled-job` | k8s CronJob (runs Python on a schedule) |

---

## Resource Budget

| Component | RAM |
|---|---|
| k3s control plane + Traefik | ~400 MB |
| Builder UI pod | ~100 MB |
| Docker Registry pod | ~50 MB |
| 5 apps × 2 envs × 128 MB | ~1.3 GB |
| Host OS + Docker daemon | ~300 MB |
| **Total** | **~2.4 GB** |
| **Headroom on 7.6 GB** | **~5.2 GB** |

---

## DNS (nip.io)

`nip.io` resolves `*.1.2.3.4.nip.io` → `1.2.3.4`. No domain purchase needed. When you get a real domain, update the Traefik IngressRoute hosts — no other changes required.

---

## Security Notes

- `builder-secret.yaml` is gitignored — never commit API keys
- Each app namespace has `NetworkPolicy` denying cross-namespace traffic
- Builder SA has ClusterRole scoped to create/manage `app-*` namespaces only
- ResourceQuota limits each app to 1 CPU, 512 MB RAM, 6 pods

---

## Sequencing

```
Step 0: git init + commit scaffold ← done
Step 1: install-k3s.sh
Step 2: setup-registry.sh
Step 3: bootstrap-cluster.sh
Step 4: builder.IP.nip.io loads
Step 5: End-to-end test with simple-api
Step 6: setup-claude-cli.sh (optional)
```
