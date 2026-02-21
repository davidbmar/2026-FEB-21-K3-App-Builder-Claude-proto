"""
k8s_ops.py — kubectl wrapper and namespace/deployment lifecycle management.
All kubectl calls run as subprocess so they use the mounted kubeconfig.
"""
import json
import subprocess
import os
from pathlib import Path
from typing import Generator, Optional
from jinja2 import Environment, FileSystemLoader

_default_manifests = Path(__file__).parent.parent / "k8s" / "templates"
MANIFESTS_DIR = Path(os.environ.get("K8S_TEMPLATES_DIR", str(_default_manifests)))
REGISTRY_HOST = os.environ.get("REGISTRY_HOST", "localhost:5050")
SERVER_IP = os.environ.get("SERVER_IP", "127.0.0.1")


def _jinja_env() -> Environment:
    return Environment(loader=FileSystemLoader(str(MANIFESTS_DIR)))


def _kubectl(args: list[str], input_text: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["kubectl"] + args
    result = subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed:\n{result.stderr}")
    return result


def _render(template_name: str, **ctx) -> str:
    env = _jinja_env()
    tmpl = env.get_template(template_name)
    return tmpl.render(**ctx)


# ---------------------------------------------------------------------------
# Namespace lifecycle
# ---------------------------------------------------------------------------

def create_namespace(app_name: str) -> None:
    """Create app-<name> namespace with ResourceQuota and NetworkPolicy."""
    for template in ("namespace.yaml.j2", "quota.yaml.j2", "networkpolicy.yaml.j2"):
        yaml = _render(template, app_name=app_name)
        _kubectl(["apply", "-f", "-"], input_text=yaml)


def delete_namespace(app_name: str) -> None:
    """Delete app-<name> namespace (and all resources within)."""
    _kubectl(["delete", "namespace", f"app-{app_name}", "--ignore-not-found=true"])


def namespace_exists(app_name: str) -> bool:
    result = _kubectl(["get", "namespace", f"app-{app_name}"], check=False)
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Deployment lifecycle
# ---------------------------------------------------------------------------

def deploy_app(app_name: str, env: str, version: str) -> None:
    """Apply Deployment + Service + Ingress for the given env (preview/prod)."""
    yaml = _render(
        "deployment.yaml.j2",
        app_name=app_name,
        env=env,
        version=version,
        registry_host=REGISTRY_HOST,
        server_ip=SERVER_IP,
    )
    _kubectl(["apply", "-f", "-"], input_text=yaml)


def set_image(app_name: str, env: str, version: str) -> None:
    """Update the container image for an existing deployment (promote)."""
    image = f"{REGISTRY_HOST}/{app_name}:{version}"
    _kubectl([
        "set", "image",
        f"deployment/{app_name}-{env}",
        f"app={image}",
        "-n", f"app-{app_name}",
    ])


def rollout_status(app_name: str, env: str, timeout: int = 90) -> bool:
    """Wait for rollout. Returns True on success, False on timeout/failure."""
    result = _kubectl([
        "rollout", "status",
        f"deployment/{app_name}-{env}",
        "-n", f"app-{app_name}",
        f"--timeout={timeout}s",
    ], check=False)
    return result.returncode == 0


def rollout_undo(app_name: str, env: str) -> None:
    """Roll back to the previous revision."""
    _kubectl([
        "rollout", "undo",
        f"deployment/{app_name}-{env}",
        "-n", f"app-{app_name}",
    ])


def create_env_configmap(app_name: str, env_vars: dict) -> None:
    """Create or update the app's ConfigMap with environment variables."""
    # Build --from-literal args
    literals = [f"--from-literal={k}={v}" for k, v in env_vars.items()]
    cmd = [
        "create", "configmap", f"{app_name}-env",
        "-n", f"app-{app_name}",
        "--save-config",
        "--dry-run=client",
        "-o", "yaml",
    ] + literals
    result = _kubectl(cmd)
    _kubectl(["apply", "-f", "-"], input_text=result.stdout)


# ---------------------------------------------------------------------------
# Status queries
# ---------------------------------------------------------------------------

def get_pod_status(app_name: str, env: str) -> dict:
    """Return pod phase, restart count, and ready status."""
    result = _kubectl([
        "get", "pods",
        "-n", f"app-{app_name}",
        "-l", f"app={app_name},env={env}",
        "-o", "json",
    ], check=False)
    if result.returncode != 0:
        return {"phase": "Unknown", "restarts": 0, "ready": False}

    data = json.loads(result.stdout)
    items = data.get("items", [])
    if not items:
        return {"phase": "Pending", "restarts": 0, "ready": False}

    pod = items[0]
    phase = pod.get("status", {}).get("phase", "Unknown")
    containers = pod.get("status", {}).get("containerStatuses", [])
    restarts = sum(c.get("restartCount", 0) for c in containers)
    ready = all(c.get("ready", False) for c in containers) if containers else False
    return {"phase": phase, "restarts": restarts, "ready": ready}


def get_all_app_statuses() -> dict:
    """Return a dict of app_name → {preview: {...}, prod: {...}} status."""
    result = _kubectl(["get", "namespaces", "-o", "json"], check=False)
    if result.returncode != 0:
        return {}

    data = json.loads(result.stdout)
    statuses = {}
    for ns in data.get("items", []):
        name = ns["metadata"]["name"]
        if not name.startswith("app-"):
            continue
        app_name = name[4:]
        statuses[app_name] = {
            "preview": get_pod_status(app_name, "preview"),
            "prod": get_pod_status(app_name, "prod"),
        }
    return statuses


def stream_pod_logs(app_name: str, env: str) -> Generator[str, None, None]:
    """Stream logs from the app pod. Yields log lines."""
    proc = subprocess.Popen(
        [
            "kubectl", "logs", "-f",
            "-n", f"app-{app_name}",
            "-l", f"app={app_name},env={env}",
            "--tail=100",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    for line in iter(proc.stdout.readline, ""):
        yield line
    proc.wait()


def get_deployment_image(app_name: str, env: str) -> Optional[str]:
    """Return the current container image tag for a deployment."""
    result = _kubectl([
        "get", "deployment", f"{app_name}-{env}",
        "-n", f"app-{app_name}",
        "-o", "jsonpath={.spec.template.spec.containers[0].image}",
    ], check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def grant_builder_access(app_name: str) -> None:
    """Create a RoleBinding giving builder-controller full access to app-<name>."""
    rb_yaml = f"""
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: builder-controller-binding
  namespace: app-{app_name}
subjects:
- kind: ServiceAccount
  name: builder-controller
  namespace: builder-system
roleRef:
  kind: ClusterRole
  name: builder-app-manager
  apiGroup: rbac.authorization.k8s.io
"""
    _kubectl(["apply", "-f", "-"], input_text=rb_yaml)
