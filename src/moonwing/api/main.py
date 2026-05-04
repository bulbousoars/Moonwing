from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from moonwing.api.routes.runs import router as runs_router
from moonwing.api.routes.targets import router as targets_router
from moonwing.api.routes.credentials import router as credentials_router
from moonwing.api.routes.findings import router as findings_router
from moonwing.api.routes.runtime_profiles import router as profiles_router
from moonwing.api.routes.users import router as users_router
from moonwing.api.routes.artifacts import router as artifacts_router
from moonwing.api.routes.dashboard import router as dashboard_router

_BASE = Path(__file__).resolve().parent

app = FastAPI(title='moonwing')

app.mount('/static', StaticFiles(directory=_BASE / 'static'), name='static')

# API routes
app.include_router(runs_router, prefix='/api/runs', tags=['runs'])
app.include_router(targets_router, prefix='/api/targets', tags=['targets'])
app.include_router(credentials_router, prefix='/api/credentials', tags=['credentials'])
app.include_router(findings_router, prefix='/api/findings', tags=['findings'])
app.include_router(profiles_router, prefix='/api/runtime-profiles', tags=['runtime-profiles'])
app.include_router(users_router, prefix='/api/users', tags=['users'])
app.include_router(artifacts_router, prefix='/api/artifacts', tags=['artifacts'])

# Dashboard (HTML)
app.include_router(dashboard_router)
