# k3s App Builder Platform

**Describe → Preview → Publish** — A self-contained platform for deploying apps with plain language + Claude, entirely within k3s on a single EC2 instance.

Project: `2026-FEB-21-K3-App-Builder-Claude-proto`

---

## Quick Start

### Prerequisites
- Ubuntu 24.04 EC2 instance (7.6 GB+ RAM, 84 GB disk)
- Docker installed
- Port 80 open in your EC2 Security Group (inbound HTTP)
- `ANTHROPIC_API_KEY` available

### Setup (run in order)

```bash
git clone https://github.com/davidbmar/2026-FEB-21-K3-App-Builder-Claude-proto
cd 2026-FEB-21-K3-App-Builder-Claude-proto
chmod +x scripts/*.sh

# Step 1: Install k3s with Traefik
./scripts/install-k3s.sh

# Step 2: Start local Docker registry
./scripts/setup-registry.sh

# Step 3: Build and deploy everything
export ANTHROPIC_API_KEY=sk-ant-...
./scripts/bootstrap-cluster.sh
```

Then open `http://builder.YOUR_PUBLIC_IP.nip.io/` in your browser.

---

## How to Use

1. Open the Builder UI in your browser
2. Click **+ New App**, choose a template and enter an app name
3. On the app detail page, describe what you want Claude to build
4. Click **Generate** — Claude streams the code live
5. Click **Build & Deploy Preview** — docker build runs, pod deploys
6. Visit the preview URL to test your app
7. Click **Publish to Production** to go live
8. Use **Rollback** if something breaks

---

## Architecture

### The Physical Machine

Everything runs on one EC2 instance. There is no separate database server, no external load balancer, nothing you pay for beyond the EC2 instance itself. The builder UI, every user app, the traffic routing, and the container registry all run on this single machine.

### Docker vs k3s

**Docker** (running directly on the host) is used only for building images. When you click Build, the builder runs `docker build` on the host machine, which reads the Dockerfile Claude generated, packages the app into a container image, and pushes it to the local registry at `localhost:5050`.

**k3s** is a lightweight Kubernetes distribution. It runs the containers that Docker built. Think of Docker as the factory that makes the container images, and k3s as the runtime that actually runs them and keeps them alive. k3s also handles networking, restarts crashed containers, and enforces resource limits.

### Traffic Flow

```
Your Browser
  → DNS lookup: myapp.52.91.12.83.nip.io  →  52.91.12.83  (nip.io magic)
  → TCP connect to EC2 port 80
  → Traefik reads Host header, matches Ingress rule
  → Forwards to myapp-prod-svc (cluster-internal Service)
  → Service routes to pod (10.42.0.x:8080)
  → Pod's process (uvicorn/nginx) handles request
  → Response travels back the same path
```

### nip.io — Free DNS Without Buying a Domain

`nip.io` is a public DNS service with one rule: any hostname of the form `anything.1.2.3.4.nip.io` resolves to `1.2.3.4`. When your browser looks up `builder.52.91.12.83.nip.io`, it gets back `52.91.12.83`. The actual routing to the right app happens on the server via Traefik, not in DNS.

When you acquire a real domain later, update the Traefik Ingress hostnames from `*.IP.nip.io` to `*.yourdomain.com` — no other changes needed.

### Traefik — The Traffic Router

Traefik binds to ports 80 and 443 on the host. Every HTTP request hits Traefik first. It reads the `Ingress` objects in Kubernetes to know where to send each request:

```
builder.IP.nip.io            →  builder-ui-svc:8000  (namespace: builder-system)
myapp.IP.nip.io              →  myapp-prod-svc:8080  (namespace: app-myapp)
myapp-preview.IP.nip.io      →  myapp-preview-svc:8080  (namespace: app-myapp)
```

No request ever touches another app. Traefik routes purely on the `Host` header.

### Namespaces — Isolation Boundaries

Kubernetes namespaces are like separate rooms. Resources in one namespace cannot see or talk to resources in another namespace.

```
kube-system       ← Traefik, CoreDNS, k3s internals
builder-system    ← Builder UI, local registry, builder service account
app-hello-world   ← Everything for the hello-world app
app-myapp         ← Everything for myapp
app-otherapp      ← Everything for otherapp
```

When you create a new app, the builder automatically creates a new namespace with a ResourceQuota and NetworkPolicy already applied.

### Each App: What's Actually Running

For an app called `myapp`, k3s runs these objects in the `app-myapp` namespace:

```
app-myapp namespace
├── Deployment: myapp-preview  →  Pod → Container (image: myapp:timestamp)
├── Deployment: myapp-prod     →  Pod → Container (image: myapp:timestamp)
├── Service: myapp-preview-svc      (stable internal address → pod)
├── Service: myapp-prod-svc         (stable internal address → pod)
├── Ingress: myapp-preview          myapp-preview.IP.nip.io → preview svc
├── Ingress: myapp-prod             myapp.IP.nip.io         → prod svc
├── ResourceQuota                   max 1 CPU, 512 MB RAM, 6 pods
└── NetworkPolicy                   blocks cross-namespace traffic
```

Each pod is a fully isolated container: its own process, its own filesystem, its own network interface. It cannot see other apps' files or network traffic.

### Preview vs Production

**Build** creates a new versioned image (`myapp:20260221.143022`) and deploys it to the **preview** environment only. Production keeps running the old version untouched.

**Publish** updates production to run the same image that preview is running. k3s does a rolling update: starts the new pod, waits for it to be healthy, then terminates the old one.

**Rollback** reverts production to the previous image tag. The old image is still in the local registry so this is instantaneous.

### ResourceQuota — Preventing Runaway Apps

Each app namespace is capped at:

```
CPU request:    500m   (half a core)
CPU limit:      1000m  (one full core)
Memory request: 256 MB
Memory limit:   512 MB
Max pods:       6
```

If a container exceeds its memory limit, the kernel kills it and k3s restarts it. This prevents one bad app from starving every other app on the machine.

### NetworkPolicy — App Isolation

Each app namespace has a policy that:
- **Allows inbound** only from pods in the same namespace and from `kube-system` (so Traefik can route to it)
- **Allows all outbound** (so apps can call external APIs, databases, etc.)

Apps cannot make direct HTTP requests to other apps on the same cluster, even if they know the internal IP.

### The Local Registry (`localhost:5050`)

A Docker registry (`registry:2`) runs as a container inside k3s. It stores every image ever built. Images are stored on the EC2 disk at `/var/lib/registry`.

```
Build time:  docker build → docker push localhost:5050/myapp:v1
Run time:    k3s pulls localhost:5050/myapp:v1 → starts container
Rollback:    k3s pulls localhost:5050/myapp:v0 → old container running again
```

### The Builder UI

The Builder UI is itself a container running in k3s (`builder-system` namespace). It is a FastAPI Python application that:

- Calls the **Anthropic API** to stream Claude-generated code
- Runs `docker build` via the Docker socket mounted from the host (`/var/run/docker.sock`)
- Runs `kubectl` commands using its ServiceAccount token (automatically mounted by k3s)
- Writes code to `/var/git/apps/<name>-workspace/` and commits it to a bare git repo at `/var/git/apps/<name>.git`
- Tracks app state in `/opt/builder/registry.json`

### Git Repos

Every app has:
- A bare git repo: `/var/git/apps/myapp.git`
- A working checkout: `/var/git/apps/myapp-workspace/`

Every code generation creates a commit, giving you a full history of what Claude generated. The workspace directory is what `docker build` uses as its build context.

---

## App Templates

| Template | Runtime | Use case |
|---|---|---|
| `simple-api` | FastAPI + Python 3.12 | REST APIs, data services, anything with endpoints |
| `static-site` | nginx | HTML/CSS/JS websites, documentation, landing pages |
| `webhook` | FastAPI + HMAC verification | GitHub webhooks, Stripe events, any signed callback |
| `scheduled-job` | Python (CronJob) | Nightly reports, data syncs, cleanup tasks |

---

## Memory Budget

| Component | RAM |
|---|---|
| k3s control plane + Traefik | ~400 MB |
| Builder UI pod | ~100 MB |
| Local registry pod | ~50 MB |
| Host OS + Docker daemon | ~300 MB |
| **Subtotal (infrastructure)** | **~850 MB** |
| 5 apps × 2 envs × 128 MB limit | ~1.3 GB |
| **Total with 10 app environments** | **~2.15 GB** |
| **Headroom on 7.6 GB** | **~5.4 GB** |

**Note on Claude Code CLI:** One Claude Code session uses approximately **900 MB – 1 GB** of RAM (it runs as a Node.js process). If you use the interactive Claude session feature from the builder, budget ~1 GB per concurrent session. On a 7.6 GB instance, limit to 1–2 concurrent Claude CLI sessions alongside normal app workloads.

---

## Project Structure

```
├── builder/                  Builder UI application
│   ├── main.py               FastAPI app — all routes
│   ├── k8s_ops.py            kubectl wrapper (namespace/deploy lifecycle)
│   ├── claude_ops.py         Claude API streaming code generation
│   ├── build_ops.py          docker build + push, git operations
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── templates/            Jinja2 HTML templates
│   │   ├── base.html
│   │   ├── home.html         App grid with status badges
│   │   ├── new_app.html      Template chooser + app name form
│   │   ├── app_detail.html   Chat, build log stream, deploy controls
│   │   └── about.html        Architecture documentation (this page)
│   ├── static/
│   │   └── style.css
│   └── claude_prompts/       System prompts per template type
│       ├── simple-api.md
│       ├── static-site.md
│       ├── webhook.md
│       └── scheduled-job.md
│
├── k8s/
│   ├── bootstrap/            Applied once during setup
│   │   ├── builder-namespace.yaml
│   │   ├── builder-rbac.yaml
│   │   ├── builder-secret.yaml   (gitignored — add your API key)
│   │   ├── registry-deployment.yaml
│   │   └── builder-deployment.yaml
│   └── templates/            Jinja2 YAML for user apps
│       ├── namespace.yaml.j2
│       ├── deployment.yaml.j2
│       ├── quota.yaml.j2
│       ├── networkpolicy.yaml.j2
│       └── cronjob.yaml.j2
│
├── app_templates/            Scaffold files per template
│   ├── simple-api/
│   ├── static-site/
│   ├── webhook/
│   └── scheduled-job/
│
└── scripts/
    ├── install-k3s.sh
    ├── setup-registry.sh
    ├── bootstrap-cluster.sh
    └── setup-claude-cli.sh
```

---

## RBAC — Who Can Do What

The Builder UI runs as a Kubernetes ServiceAccount called `builder-controller` in the `builder-system` namespace. It has a `ClusterRole` that allows it to:

- Read all namespaces (to list apps)
- Create/delete `app-*` namespaces
- Create/update Deployments, Services, Ingresses, ConfigMaps inside `app-*` namespaces
- Create RoleBindings inside `app-*` namespaces

It cannot touch `kube-system`, cannot `exec` into pods, and cannot modify `builder-system` itself.

---

## Security Notes

- `builder-secret.yaml` is gitignored — never commit your API key
- Each app namespace has NetworkPolicy blocking cross-namespace traffic
- ResourceQuota limits each app to prevent resource exhaustion
- The Docker socket mount (`/var/run/docker.sock`) gives the builder root-equivalent access to Docker on the host — this is intentional for a single-tenant dev platform, but should be reviewed for multi-tenant production use

---

## Setup Sequence

```
Step 0  git clone + repo exists                    ← done
Step 1  scripts/install-k3s.sh                     k3s + Traefik on 80/443
Step 2  scripts/setup-registry.sh                  registry on localhost:5050
Step 3  export ANTHROPIC_API_KEY=...
        scripts/bootstrap-cluster.sh               build + deploy builder UI
Step 4  http://builder.YOUR_IP.nip.io/ loads       ← you are here
Step 5  Create an app, describe it, generate, build, preview, publish
Step 6  scripts/setup-claude-cli.sh                (optional) interactive Claude sessions
```
