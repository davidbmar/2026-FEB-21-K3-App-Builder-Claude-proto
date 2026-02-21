"""
main.py — FastAPI Builder UI: all routes.
"""
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import build_ops
import claude_ops
import k8s_ops

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REGISTRY_FILE = Path(os.environ.get("REGISTRY_FILE", "/opt/builder/registry.json"))
SERVER_IP = os.environ.get("SERVER_IP", "127.0.0.1")
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

VALID_TEMPLATES = ["static-site", "simple-api", "webhook", "scheduled-job"]

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="k3s App Builder")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

def _load_registry() -> dict:
    if REGISTRY_FILE.exists():
        return json.loads(REGISTRY_FILE.read_text())
    return {}


def _save_registry(data: dict) -> None:
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_FILE.write_text(json.dumps(data, indent=2))


def _get_app(name: str) -> dict:
    reg = _load_registry()
    if name not in reg:
        raise HTTPException(status_code=404, detail=f"App '{name}' not found")
    return reg[name]


def _app_url(app_name: str, env: str) -> str:
    suffix = "-preview" if env == "preview" else ""
    return f"http://{app_name}{suffix}.{SERVER_IP}.nip.io/"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    registry = _load_registry()
    apps = list(registry.values())

    # Enrich with live k8s status
    k8s_statuses = k8s_ops.get_all_app_statuses()
    for a in apps:
        name = a["app_name"]
        a["k8s"] = k8s_statuses.get(name, {})

    return templates.TemplateResponse("home.html", {"request": request, "apps": apps})


@app.get("/new", response_class=HTMLResponse)
async def new_app_form(request: Request):
    return templates.TemplateResponse(
        "new_app.html",
        {"request": request, "templates_list": VALID_TEMPLATES},
    )


@app.post("/create")
async def create_app(
    request: Request,
    app_name: str = Form(...),
    template: str = Form(...),
    description: str = Form(""),
):
    app_name = app_name.strip().lower().replace(" ", "-")
    if not app_name.isidentifier() and not all(c.isalnum() or c == "-" for c in app_name):
        raise HTTPException(status_code=400, detail="Invalid app name")
    if template not in VALID_TEMPLATES:
        raise HTTPException(status_code=400, detail="Invalid template")

    registry = _load_registry()
    if app_name in registry:
        raise HTTPException(status_code=409, detail=f"App '{app_name}' already exists")

    # Create k8s namespace
    k8s_ops.create_namespace(app_name)
    k8s_ops.grant_builder_access(app_name)
    k8s_ops.create_env_configmap(app_name, {"APP_NAME": app_name})

    # Init git repo + scaffold
    build_ops.init_git_repo(app_name)
    build_ops.scaffold_app(app_name, template)

    # Save to registry
    registry[app_name] = {
        "app_name": app_name,
        "template": template,
        "description": description,
        "git_repo": f"/var/git/apps/{app_name}.git",
        "preview_version": None,
        "prod_version": None,
        "preview_url": _app_url(app_name, "preview"),
        "prod_url": _app_url(app_name, "prod"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "created",
    }
    _save_registry(registry)

    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/app/{app_name}", status_code=303)


@app.get("/app/{name}", response_class=HTMLResponse)
async def app_detail(request: Request, name: str):
    app_entry = _get_app(name)
    files = build_ops.get_workspace_files(name)
    k8s_status = k8s_ops.get_all_app_statuses().get(name, {})
    return templates.TemplateResponse(
        "app_detail.html",
        {
            "request": request,
            "app": app_entry,
            "files": files,
            "k8s_status": k8s_status,
            "server_ip": SERVER_IP,
        },
    )


@app.post("/app/{name}/generate")
async def generate_code(name: str, description: str = Form(...)):
    """Stream Claude code generation as SSE."""
    app_entry = _get_app(name)
    template = app_entry["template"]
    current_files = build_ops.get_workspace_files(name)

    async def event_stream() -> AsyncGenerator[str, None]:
        full_text = ""
        loop = asyncio.get_event_loop()

        # Run blocking stream in thread pool
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            queue: asyncio.Queue = asyncio.Queue()

            def run_stream():
                try:
                    for chunk in claude_ops.generate_code_stream(
                        name, template, description, current_files
                    ):
                        asyncio.run_coroutine_threadsafe(queue.put(("chunk", chunk)), loop)
                    asyncio.run_coroutine_threadsafe(queue.put(("done", None)), loop)
                except Exception as e:
                    asyncio.run_coroutine_threadsafe(queue.put(("error", str(e))), loop)

            pool.submit(run_stream)

            while True:
                event_type, data = await queue.get()
                if event_type == "chunk":
                    full_text += data
                    escaped = data.replace("\n", "\\n").replace("\r", "\\r")
                    yield f"data: {json.dumps({'chunk': escaped})}\n\n"
                elif event_type == "done":
                    # Extract and commit files
                    files = claude_ops._extract_files(full_text)
                    if files:
                        build_ops.write_files_to_workspace(name, files)
                        file_list = list(files.keys())
                    else:
                        file_list = []
                    yield f"data: {json.dumps({'done': True, 'files': file_list})}\n\n"
                    break
                elif event_type == "error":
                    yield f"data: {json.dumps({'error': data})}\n\n"
                    break

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/app/{name}/build")
async def build_app(name: str):
    """Stream docker build + kubectl apply as SSE."""
    app_entry = _get_app(name)
    version = build_ops.make_version()

    async def event_stream() -> AsyncGenerator[str, None]:
        loop = asyncio.get_event_loop()
        import concurrent.futures

        queue: asyncio.Queue = asyncio.Queue()

        def run_build():
            try:
                for line in build_ops.build_and_push(name, version):
                    asyncio.run_coroutine_threadsafe(queue.put(("log", line)), loop)

                # Deploy to preview
                asyncio.run_coroutine_threadsafe(queue.put(("log", "=== Deploying to preview ===\n")), loop)
                k8s_ops.deploy_app(name, "preview", version)
                ok = k8s_ops.rollout_status(name, "preview")

                if ok:
                    asyncio.run_coroutine_threadsafe(queue.put(("done", version)), loop)
                else:
                    asyncio.run_coroutine_threadsafe(queue.put(("error", "Rollout timed out")), loop)
            except Exception as e:
                asyncio.run_coroutine_threadsafe(queue.put(("error", str(e))), loop)

        with concurrent.futures.ThreadPoolExecutor() as pool:
            pool.submit(run_build)

            while True:
                event_type, data = await queue.get()
                if event_type == "log":
                    yield f"data: {json.dumps({'log': data})}\n\n"
                elif event_type == "done":
                    # Update registry
                    registry = _load_registry()
                    registry[name]["preview_version"] = data
                    registry[name]["status"] = "preview"
                    _save_registry(registry)
                    preview_url = _app_url(name, "preview")
                    yield f"data: {json.dumps({'done': True, 'version': data, 'preview_url': preview_url})}\n\n"
                    break
                elif event_type == "error":
                    yield f"data: {json.dumps({'error': data})}\n\n"
                    break

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/app/{name}/publish")
async def publish_app(name: str):
    """Promote preview → prod."""
    registry = _load_registry()
    app_entry = _get_app(name)

    preview_version = app_entry.get("preview_version")
    if not preview_version:
        raise HTTPException(status_code=400, detail="No preview build to publish")

    # Save current prod as rollback target
    current_prod = app_entry.get("prod_version")

    # Deploy to prod (creates if not exists, updates if exists)
    k8s_ops.deploy_app(name, "prod", preview_version)
    ok = k8s_ops.rollout_status(name, "prod")

    if not ok:
        if current_prod:
            k8s_ops.set_image(name, "prod", current_prod)
        raise HTTPException(status_code=500, detail="Prod rollout failed, reverted")

    registry[name]["prod_version"] = preview_version
    registry[name]["rollback_version"] = current_prod
    registry[name]["status"] = "published"
    _save_registry(registry)

    return JSONResponse({
        "ok": True,
        "version": preview_version,
        "prod_url": _app_url(name, "prod"),
    })


@app.post("/app/{name}/rollback")
async def rollback_app(name: str):
    """Roll back prod deployment."""
    app_entry = _get_app(name)
    rollback_version = app_entry.get("rollback_version")

    if rollback_version:
        k8s_ops.set_image(name, "prod", rollback_version)
        ok = k8s_ops.rollout_status(name, "prod")
    else:
        k8s_ops.rollout_undo(name, "prod")
        ok = k8s_ops.rollout_status(name, "prod")

    registry = _load_registry()
    if ok and rollback_version:
        registry[name]["prod_version"] = rollback_version
        registry[name]["rollback_version"] = None
        _save_registry(registry)

    return JSONResponse({"ok": ok})


@app.get("/app/{name}/logs")
async def app_logs(name: str, env: str = "preview"):
    """SSE stream of pod logs."""
    _get_app(name)

    async def event_stream() -> AsyncGenerator[str, None]:
        loop = asyncio.get_event_loop()
        import concurrent.futures

        queue: asyncio.Queue = asyncio.Queue()

        def stream_logs():
            try:
                for line in k8s_ops.stream_pod_logs(name, env):
                    asyncio.run_coroutine_threadsafe(queue.put(line), loop)
            except Exception as e:
                asyncio.run_coroutine_threadsafe(queue.put(f"ERROR: {e}\n"), loop)

        with concurrent.futures.ThreadPoolExecutor() as pool:
            pool.submit(stream_logs)
            while True:
                line = await asyncio.wait_for(queue.get(), timeout=30)
                yield f"data: {json.dumps({'log': line})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/app/{name}/status")
async def app_status(name: str):
    """JSON: pod phase, restarts, URLs."""
    app_entry = _get_app(name)
    preview_status = k8s_ops.get_pod_status(name, "preview")
    prod_status = k8s_ops.get_pod_status(name, "prod")
    return JSONResponse({
        "preview": {**preview_status, "url": app_entry.get("preview_url")},
        "prod": {**prod_status, "url": app_entry.get("prod_url")},
        "preview_version": app_entry.get("preview_version"),
        "prod_version": app_entry.get("prod_version"),
    })


@app.delete("/app/{name}")
async def delete_app(name: str):
    """Teardown namespace + git repo + registry entry."""
    _get_app(name)

    k8s_ops.delete_namespace(name)
    build_ops.delete_git_repo(name)

    registry = _load_registry()
    del registry[name]
    _save_registry(registry)

    return JSONResponse({"ok": True})


@app.post("/app/{name}/claude-session")
async def launch_claude_session(name: str):
    """
    Prepare workspace and return info for launching a tmux/claude session.
    The actual tmux session must be started on the host by the user or a sidecar.
    """
    _get_app(name)
    import subprocess as sp
    workspace = f"/var/git/apps/{name}-workspace"

    # Ensure workspace exists
    build_ops.checkout_workspace(name)

    session_name = f"claude-{name}"
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    # Create tmux session if it doesn't exist
    check = sp.run(["tmux", "has-session", "-t", session_name], capture_output=True)
    if check.returncode != 0:
        sp.run(["tmux", "new-session", "-d", "-s", session_name, "-c", workspace], check=True)
        sp.run([
            "tmux", "send-keys", "-t", session_name,
            f"export ANTHROPIC_API_KEY={api_key} && claude", "Enter",
        ], check=True)

    return JSONResponse({
        "session": session_name,
        "workspace": workspace,
        "message": f"tmux session '{session_name}' ready. Attach with: tmux attach -t {session_name}",
    })
