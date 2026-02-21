"""
build_ops.py — Docker build + push and git operations.
"""
import os
import subprocess
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

GIT_BASE = os.environ.get("GIT_BASE", "/var/git/apps")
REGISTRY_HOST = os.environ.get("REGISTRY_HOST", "localhost:5050")
# In the k8s pod, app_templates is mounted at /app/app_templates.
# Locally (dev), fall back to <repo>/app_templates relative to this file.
_default_templates = Path(__file__).parent.parent / "app_templates"
APP_TEMPLATES_DIR = Path(os.environ.get("APP_TEMPLATES_DIR", str(_default_templates)))


class BuildError(Exception):
    pass


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------

def make_version() -> str:
    """Return a timestamp-based version string: 20260221.143022"""
    return datetime.now(timezone.utc).strftime("%Y%m%d.%H%M%S")


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------

def init_git_repo(app_name: str) -> None:
    """Create a bare git repo at GIT_BASE/<app_name>.git."""
    repo_path = Path(GIT_BASE) / f"{app_name}.git"
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare", str(repo_path)], check=True, capture_output=True)


def scaffold_app(app_name: str, template: str) -> None:
    """
    Copy template files into a workspace and make the initial commit.
    Workspace: GIT_BASE/<app_name>-workspace/
    """
    workspace = Path(GIT_BASE) / f"{app_name}-workspace"
    repo = Path(GIT_BASE) / f"{app_name}.git"
    template_dir = APP_TEMPLATES_DIR / template

    if not template_dir.exists():
        raise ValueError(f"Unknown template: {template}")

    # Fresh workspace
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)

    # Init workspace as a new git repo pointing at the bare repo as origin
    subprocess.run(["git", "-C", str(workspace), "init", "-b", "main"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(workspace), "remote", "add", "origin", str(repo)],
                   check=True, capture_output=True)

    # Copy template files
    for src in template_dir.iterdir():
        dest = workspace / src.name
        if src.is_file():
            shutil.copy2(src, dest)
        else:
            shutil.copytree(src, dest)

    # Git config + initial commit
    env = os.environ.copy()
    env.update({"GIT_AUTHOR_NAME": "Builder", "GIT_AUTHOR_EMAIL": "builder@local",
                 "GIT_COMMITTER_NAME": "Builder", "GIT_COMMITTER_EMAIL": "builder@local"})
    subprocess.run(["git", "-C", str(workspace), "add", "."], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(workspace), "commit", "-m", f"Scaffold {template} for {app_name}"],
        check=True, env=env, capture_output=True,
    )
    # Push to bare repo; -u sets tracking so subsequent pushes work
    subprocess.run(["git", "-C", str(workspace), "push", "-u", "origin", "main"],
                   check=True, env=env, capture_output=True)


def write_files_to_workspace(app_name: str, files: dict[str, str]) -> None:
    """Write generated files into the workspace and commit."""
    workspace = Path(GIT_BASE) / f"{app_name}-workspace"
    if not workspace.exists():
        raise RuntimeError(f"Workspace not found: {workspace}")

    for filename, content in files.items():
        file_path = workspace / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)

    env = os.environ.copy()
    env.update({"GIT_AUTHOR_NAME": "Builder", "GIT_AUTHOR_EMAIL": "builder@local",
                 "GIT_COMMITTER_NAME": "Builder", "GIT_COMMITTER_EMAIL": "builder@local"})
    subprocess.run(["git", "-C", str(workspace), "add", "."], check=True, env=env)

    # Only commit if there are staged changes
    result = subprocess.run(
        ["git", "-C", str(workspace), "diff", "--cached", "--quiet"],
        env=env, capture_output=True,
    )
    if result.returncode != 0:
        subprocess.run(
            ["git", "-C", str(workspace), "commit", "-m", "Update: Claude-generated code"],
            check=True, env=env, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(workspace), "push", "-u", "origin", "main"],
            check=True, env=env, capture_output=True,
        )


def checkout_workspace(app_name: str) -> None:
    """Ensure the workspace is up to date with the bare repo HEAD."""
    workspace = Path(GIT_BASE) / f"{app_name}-workspace"
    repo = Path(GIT_BASE) / f"{app_name}.git"

    if not workspace.exists():
        # Workspace missing — recreate it
        workspace.mkdir(parents=True)
        subprocess.run(["git", "-C", str(workspace), "init", "-b", "main"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(workspace), "remote", "add", "origin", str(repo)],
                       check=True, capture_output=True)

    # Fetch and hard-reset to origin/main
    subprocess.run(["git", "-C", str(workspace), "fetch", "origin"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(workspace), "reset", "--hard", "origin/main"],
                   check=True, capture_output=True)


def delete_git_repo(app_name: str) -> None:
    """Remove the bare repo and workspace for an app."""
    for path in [
        Path(GIT_BASE) / f"{app_name}.git",
        Path(GIT_BASE) / f"{app_name}-workspace",
    ]:
        if path.exists():
            shutil.rmtree(path)


def get_workspace_files(app_name: str) -> dict[str, str]:
    """Return {filename: content} for all files in the workspace."""
    workspace = Path(GIT_BASE) / f"{app_name}-workspace"
    if not workspace.exists():
        return {}
    files = {}
    for f in workspace.rglob("*"):
        if f.is_file() and ".git" not in f.parts:
            try:
                rel = str(f.relative_to(workspace))
                files[rel] = f.read_text(errors="replace")
            except Exception:
                pass
    return files


# ---------------------------------------------------------------------------
# Docker build + push
# ---------------------------------------------------------------------------

def build_and_push(app_name: str, version: str) -> Generator[str, None, None]:
    """
    Check out HEAD, docker build, docker push.
    Yields log lines (for SSE streaming to the UI).
    Raises BuildError on failure.
    """
    workspace = Path(GIT_BASE) / f"{app_name}-workspace"
    image = f"{REGISTRY_HOST}/{app_name}:{version}"

    # Ensure workspace is up to date
    checkout_workspace(app_name)

    yield f"=== Building {image} ===\n"

    proc = subprocess.Popen(
        [
            "docker", "build",
            "--build-arg", f"APP_NAME={app_name}",
            "-t", image,
            str(workspace),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    for line in iter(proc.stdout.readline, ""):
        yield line
    proc.wait()

    if proc.returncode != 0:
        raise BuildError(f"docker build failed (exit {proc.returncode})")

    yield f"=== Pushing {image} ===\n"
    push_proc = subprocess.Popen(
        ["docker", "push", image],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    for line in iter(push_proc.stdout.readline, ""):
        yield line
    push_proc.wait()

    if push_proc.returncode != 0:
        raise BuildError(f"docker push failed (exit {push_proc.returncode})")

    yield f"=== Build complete: {image} ===\n"
