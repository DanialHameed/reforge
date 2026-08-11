from fastapi import APIRouter

router = APIRouter(prefix="/migrations")


@router.post("/run")
async def run_migrations():
    # TODO: wire Alembic migrations
    return {"ok": True}

