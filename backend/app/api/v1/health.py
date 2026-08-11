from fastapi import APIRouter

from app.core.config import settings

router = APIRouter()


@router.get("/health")
def health():
    return {"name": settings.APP_NAME, "env": settings.ENV, "version": settings.API_VERSION}

