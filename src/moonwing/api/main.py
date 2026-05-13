from pathlib import Path
from uuid import UUID
import logging

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from moonwing.api.deps import _get_session_factory, _get_settings
from moonwing.api.routes.credentials import router as credentials_router
from moonwing.api.routes.dashboard import router as dashboard_router
from moonwing.api.routes.runs import router as runs_router
from moonwing.api.routes.sensors import router as sensor_router
from moonwing.api.host_terminal import register_host_terminal
from moonwing.api.routes.system_updates_api import router as system_updates_router
from moonwing.db.models import User
from moonwing.services.ai_provider_probe import log_ai_cli_boot_diagnostics
from moonwing.services.auth import parse_session_token
from moonwing.services.iam import authenticate_service_token, bootstrap_admin
from moonwing.services.permissions import permission_for_request, require_role

_BASE = Path(__file__).resolve().parent

app = FastAPI(title='moonwing')

register_host_terminal(app)

app.mount('/static', StaticFiles(directory=_BASE / 'static'), name='static')


PUBLIC_PATHS = {
    "/login",
    "/logout",
    "/auth/oidc/login",
    "/auth/oidc/callback",
    "/health",
}

BOOTSTRAP_ALLOWED_PATHS = {
    "/setup/admin",
    "/logout",
    "/health",
}


@app.on_event("startup")
def _startup() -> None:
    settings = _get_settings()
    if settings.log_json_to_stdout:
        from moonwing.services.logging_json import configure_json_stdout_logging

        configure_json_stdout_logging()
    _ = log_ai_cli_boot_diagnostics(
        logging.getLogger("moonwing.api"),
        settings,
        process_label="moonwing-api",
    )
    session = _get_session_factory()()
    try:
        bootstrap_admin(session, _get_settings())
    finally:
        session.close()


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/static"):
        return await call_next(request)
    if path.startswith("/api/sensors"):
        return await call_next(request)

    session = _get_session_factory()()
    try:
        user = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            user = authenticate_service_token(session, auth_header.split(" ", 1)[1].strip())
        else:
            payload = parse_session_token(
                request.cookies.get("moonwing_session"),
                secret=_get_settings().session_secret,
            )
            if payload:
                try:
                    user = session.get(User, UUID(payload["sub"]))
                except ValueError:
                    user = None
                if user is not None and user.status != "active":
                    user = None

        if user is None:
            if path.startswith("/api"):
                return JSONResponse({"detail": "Authentication required"}, status_code=401)
            return RedirectResponse(url=f"/login?next={path}", status_code=303)

        required_permission = permission_for_request(path, request.method)
        if required_permission:
            try:
                require_role(user.role, required_permission)
            except PermissionError as exc:
                if path.startswith("/api"):
                    return JSONResponse({"detail": str(exc)}, status_code=403)
                return RedirectResponse(url="/", status_code=303)

        request.state.current_user = {
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
            "is_service_account": user.is_service_account,
            "is_bootstrap": user.is_bootstrap,
            "must_change_password": user.must_change_password,
        }

        if user.is_bootstrap and path not in BOOTSTRAP_ALLOWED_PATHS and not path.startswith("/static"):
            if path.startswith("/api"):
                return JSONResponse({"detail": "First-run setup required"}, status_code=403)
            return RedirectResponse(url="/setup/admin", status_code=303)
    finally:
        session.close()

    return await call_next(request)


@app.get("/health")
def health():
    return {"ok": True}

app.include_router(credentials_router, prefix='/api/credentials', tags=['credentials'])
app.include_router(runs_router, prefix='/api/runs', tags=['runs'])
app.include_router(sensor_router, prefix='/api/sensors', tags=['sensors'])
app.include_router(system_updates_router, prefix='/api/system', tags=['system'])

# Dashboard (HTML)
app.include_router(dashboard_router)
