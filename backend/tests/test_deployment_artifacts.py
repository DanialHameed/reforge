"""Production deployment blockers (P-1, P-2, P-5, P-8, P-9): static checks
on the deployment artifacts.

These are not unit tests — they are infrastructure invariants. They run as
part of the normal pytest suite so any regression in the deploy.yml /
docker-compose.prod.yml / Dockerfile is caught at CI time, not at first
production deploy.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(REPO_ROOT / "backend"))

PROD_COMPOSE_PATH = REPO_ROOT / "docker-compose.prod.yml"
DEPLOY_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
DOCKERFILE_PATH = REPO_ROOT / "backend" / "Dockerfile"
PROD_ENV_EXAMPLE_PATH = REPO_ROOT / ".env.production.example"


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fp:
        return yaml.safe_load(fp)


# ---------------------------------------------------------------------------
# docker-compose.prod.yml
# ---------------------------------------------------------------------------


def test_prod_compose_exists() -> None:
    """P-1: file referenced by deploy.yml must exist in the repo."""
    assert PROD_COMPOSE_PATH.is_file(), (
        f"docker-compose.prod.yml is missing — deploy.yml references it on line `up -d --build`. "
        f"Without it, the SSH deploy step fails on every push to main."
    )


def test_prod_compose_is_valid_yaml() -> None:
    data = _load_yaml(PROD_COMPOSE_PATH)
    assert isinstance(data, dict)
    assert "services" in data, "compose file must define a top-level `services` key"


def test_prod_compose_defines_required_services() -> None:
    """P-2 / P-4: prod compose must include the full production stack."""
    data = _load_yaml(PROD_COMPOSE_PATH)
    services = set((data.get("services") or {}).keys())
    required = {"postgres", "redis", "api", "worker", "beat", "nginx"}
    missing = required - services
    assert not missing, f"docker-compose.prod.yml is missing services: {sorted(missing)}"


def test_prod_compose_nginx_proxies_api_and_has_healthcheck() -> None:
    """P-4: nginx must depend on api healthcheck, expose port 80, mount the
    production config, and have its own healthcheck.
    """
    data = _load_yaml(PROD_COMPOSE_PATH)
    nginx = data["services"]["nginx"]

    # Image is pinned (no :latest) so deploys are deterministic.
    image = nginx.get("image", "")
    assert image.startswith("nginx:") and image != "nginx:latest", (
        f"nginx image must be pinned to a real version, got {image!r}"
    )

    # Depends on api being healthy so traffic is never accepted before the
    # upstream has finished starting.
    depends = nginx.get("depends_on") or {}
    assert depends.get("api", {}).get("condition") == "service_healthy", (
        "nginx must depend on api being healthy"
    )

    # Port 80 is published.
    ports = nginx.get("ports") or []
    assert any(":80" in str(p) for p in ports), "nginx must publish port 80"

    # Config is mounted read-only.
    volumes = nginx.get("volumes") or []
    assert any(
        "nginx.prod.conf" in str(v) and ":ro" in str(v) for v in volumes
    ), "nginx must mount nginx.prod.conf read-only"

    # Healthcheck is present.
    assert "healthcheck" in nginx, "nginx must declare a healthcheck"


def test_nginx_prod_conf_exists_and_enforces_upload_limit() -> None:
    """The nginx config must exist and cap request bodies at 2 GB to match
    the API's documented ``MAX_VIDEO_BYTES`` cap. A smaller cap silently
    breaks legitimate large uploads; no cap exposes the upstream to abuse.
    """
    conf = REPO_ROOT / "nginx" / "nginx.prod.conf"
    assert conf.is_file(), "nginx/nginx.prod.conf is required for the production stack"
    raw = conf.read_text(encoding="utf-8")
    assert re.search(r"client_max_body_size\s+2g", raw, flags=re.IGNORECASE), (
        "nginx config must cap request bodies at 2g to match the API upload limit"
    )
    # Defense-in-depth security headers must be present.
    for header in ("X-Frame-Options", "X-Content-Type-Options", "Referrer-Policy"):
        assert header in raw, f"nginx config missing security header: {header}"
    # Health endpoint must be exposed for external probes.
    assert re.search(r"location\s*=?\s*/health", raw), (
        "nginx config must expose /health for external probes"
    )


def test_prod_compose_persists_critical_volumes() -> None:
    """P-2 / P-6: postgres, redis and uploads must use named volumes so data
    survives container restart.
    """
    data = _load_yaml(PROD_COMPOSE_PATH)
    services = data.get("services") or {}
    volumes = data.get("volumes") or {}

    for vol in ("postgres_data", "redis_data", "uploads_data"):
        assert vol in volumes, f"named volume `{vol}` missing from top-level volumes"

    pg_volumes = services.get("postgres", {}).get("volumes") or []
    assert any(v.startswith("postgres_data:") for v in pg_volumes), (
        "postgres service must mount the postgres_data named volume"
    )

    redis_volumes = services.get("redis", {}).get("volumes") or []
    assert any(v.startswith("redis_data:") for v in redis_volumes), (
        "redis service must mount the redis_data named volume"
    )

    api_volumes = services.get("api", {}).get("volumes") or []
    assert any("uploads_data:" in v for v in api_volumes), (
        "api service must mount uploads_data so the local-fallback uploads dir persists"
    )


def test_prod_compose_disables_celery_eager_mode() -> None:
    """P-5: production compose must explicitly turn off eager mode in
    api / worker / beat. The Settings default is True; without the override
    every Celery dispatch would block the API request thread.
    """
    data = _load_yaml(PROD_COMPOSE_PATH)
    services = data.get("services") or {}
    for svc_name in ("api", "worker", "beat"):
        env = services[svc_name].get("environment") or {}
        # Compose may surface the value as bool, str, or YAML scalar.
        always_eager = str(env.get("CELERY_TASK_ALWAYS_EAGER")).lower()
        assert always_eager == "false", (
            f"{svc_name}.environment.CELERY_TASK_ALWAYS_EAGER must be 'false' "
            f"(got {env.get('CELERY_TASK_ALWAYS_EAGER')!r})"
        )
        env_value = str(env.get("ENV") or "").lower()
        assert env_value == "production", (
            f"{svc_name}.environment.ENV must be 'production' (got {env.get('ENV')!r})"
        )


def test_prod_compose_worker_uses_prefork_pool() -> None:
    """P-8: production worker must NOT use --pool=solo."""
    data = _load_yaml(PROD_COMPOSE_PATH)
    cmd = data["services"]["worker"]["command"]
    cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
    assert "--pool=prefork" in cmd_str, (
        f"worker.command must use `--pool=prefork`; got: {cmd_str!r}"
    )
    assert "--pool=solo" not in cmd_str, (
        f"worker.command must NOT use `--pool=solo` in production; got: {cmd_str!r}"
    )


def test_prod_compose_has_celery_beat_service() -> None:
    """The `reforge.check_and_publish` periodic task only runs if a beat
    process is alive. Audit P-2 explicitly called this out as missing.
    """
    data = _load_yaml(PROD_COMPOSE_PATH)
    beat_cmd = data["services"]["beat"]["command"]
    cmd_str = beat_cmd if isinstance(beat_cmd, str) else " ".join(beat_cmd)
    assert "celery" in cmd_str and "beat" in cmd_str, (
        f"beat.command must run `celery beat`; got: {cmd_str!r}"
    )


def test_prod_compose_postgres_password_is_required() -> None:
    """Compose interpolation `${VAR:?msg}` makes Compose refuse to start
    when the variable is unset. We require POSTGRES_PASSWORD to surface
    a useful error early instead of silently launching with an empty pw.
    """
    raw = PROD_COMPOSE_PATH.read_text(encoding="utf-8")
    assert "${POSTGRES_PASSWORD:?" in raw, (
        "POSTGRES_PASSWORD must use the `${VAR:?error}` interpolation form so Compose "
        "refuses to start without a real password"
    )


# ---------------------------------------------------------------------------
# deploy.yml
# ---------------------------------------------------------------------------


def test_deploy_workflow_runs_alembic_upgrade() -> None:
    """P-9: deployment script must apply migrations before swapping containers."""
    raw = DEPLOY_WORKFLOW_PATH.read_text(encoding="utf-8")
    assert re.search(r"alembic\s+upgrade\s+head", raw), (
        "deploy.yml must run `alembic upgrade head` before bringing services up; "
        "without it, schema drift accumulates silently between releases"
    )


def test_deploy_workflow_references_real_compose_file() -> None:
    raw = DEPLOY_WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "docker-compose.prod.yml" in raw, (
        "deploy.yml must reference docker-compose.prod.yml"
    )


def test_deploy_workflow_yaml_is_valid() -> None:
    data = _load_yaml(DEPLOY_WORKFLOW_PATH)
    assert isinstance(data, dict)
    assert "jobs" in data and "deploy" in data["jobs"]


# ---------------------------------------------------------------------------
# Dockerfile
# ---------------------------------------------------------------------------


def test_dockerfile_installs_ffmpeg() -> None:
    """Media validation imports ``ffmpeg-python`` and shells out to ffprobe;
    the binary must be present in the runtime image.
    """
    raw = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert re.search(r"\bffmpeg\b", raw), (
        "Dockerfile must install the ffmpeg system binary; otherwise media "
        "validation fails with FileNotFoundError in production"
    )


def test_dockerfile_runs_as_non_root() -> None:
    raw = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert re.search(r"^USER\s+\S+", raw, flags=re.MULTILINE), (
        "Dockerfile must end on a non-root USER for defense in depth"
    )


# ---------------------------------------------------------------------------
# .env.production.example
# ---------------------------------------------------------------------------


def test_env_example_documents_critical_keys() -> None:
    raw = PROD_ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    for key in (
        "ENV",
        "CELERY_TASK_ALWAYS_EAGER",
        "POSTGRES_PASSWORD",
        "SECRET_KEY",
        "FERNET_KEY",
        "GEMINI_API_KEY",
    ):
        assert re.search(rf"^{key}\s*=", raw, flags=re.MULTILINE), (
            f"{key} must appear as a top-level assignment in .env.production.example"
        )
