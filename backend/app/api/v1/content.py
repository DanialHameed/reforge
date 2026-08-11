from __future__ import annotations

import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from pathlib import Path

import cloudinary
import cloudinary.uploader
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.auth_models import User
from app.models.content_models import (
    ContentItemResponse,
    ContentItemUpdate,
    PlatformVariantResponse,
    PlatformVariantUpdateAck,
    PlatformVariantUpdateRequest,
)
from app.models.content_orm import ContentItem, PlatformVariant
from app.repositories.content_repository import content_repository
from app.repositories.platform_variant_repository import (
    canonical_platform_key,
    platform_variant_repository,
)
from app.services import content_detail_cache as content_detail_cache_svc
from app.services.upload_validation import (
    UploadValidationError,
    assert_magic_matches,
    assert_mime_allowed,
    family_of,
    max_bytes_for,
    safe_extension_for,
    sanitize_extension,
)
from app.workers.content_processor import start_content_processing_background

router = APIRouter(prefix="/content")
logger = logging.getLogger(__name__)


# Per-MIME-family ceilings live in ``app.services.upload_validation`` now;
# kept here as historical references only.
MAX_VIDEO_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
MAX_IMAGE_BYTES = 100 * 1024 * 1024        # 100 MB


def _require_cloudinary_config() -> None:
    # Cloudinary python SDK reads CLOUDINARY_URL or cloud_name/api_key/api_secret env vars.
    if not (os.getenv("CLOUDINARY_URL") or (os.getenv("CLOUDINARY_CLOUD_NAME") and os.getenv("CLOUDINARY_API_KEY"))):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cloudinary is not configured (set CLOUDINARY_URL or CLOUDINARY_* env vars).",
        )
    cloudinary.config(secure=True)


def _mb_from_bytes(n: int) -> float:
    return float(Decimal(n) / Decimal(1024 * 1024))


async def _save_upload_to_temp(
    upload: UploadFile, *, max_bytes: int, suffix: str
) -> tuple[str, int]:
    """Stream the upload to a temp file, enforcing ``max_bytes``.

    ``suffix`` MUST come from the server-controlled allow-list
    (``upload_validation.safe_extension_for``), NEVER from the raw
    filename. Passing user-controlled data here is a path-traversal
    vector.
    """
    size = 0
    fd, path = tempfile.mkstemp(prefix="reforge_", suffix=suffix)
    os.close(fd)

    try:
        with open(path, "wb") as f:
            while True:
                chunk = await upload.read(1024 * 1024)  # 1MB
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large")
                f.write(chunk)
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    finally:
        await upload.close()

    return path, size


def _upload_to_cloudinary(path: str, resource_type: str) -> dict[str, Any]:
    if resource_type == "video":
        return cloudinary.uploader.upload_large(path, resource_type="video")
    return cloudinary.uploader.upload(path, resource_type="image")


def _uploads_dir() -> str:
    # parents[3] is the app root in both layouts this runs under: locally
    # that's `backend/` (.../backend/app/api/v1/content.py), and in the
    # Docker image it's `/app` (Dockerfile does `COPY . /app`, flattening
    # `backend/` out of the path — it also pre-creates `/app/uploads` and
    # docker-compose.prod.yml mounts a volume there). The previous
    # `parents[4]` climbed one level too far in the container — landing on
    # `/` — which the non-root `reforge` user can't write to, crashing
    # every upload with a `PermissionError` (same class of bug as the
    # `/logs` path in ai_service.py).
    return str(Path(__file__).resolve().parents[3] / "uploads")


def _ensure_uploads_dir() -> str:
    uploads = os.path.abspath(_uploads_dir())
    os.makedirs(uploads, exist_ok=True)
    return uploads


@router.get("/uploads/{filename}")
async def get_uploaded_file(filename: str):
    """Serve files from the local-fallback ``uploads/`` directory.

    The filename segment is end-user-reachable (the upload endpoint
    publishes the URL into ``ContentItem.original_file_url``). We must
    therefore reject any value that escapes the uploads directory:

        * ``..`` parent-traversal
        * absolute paths (``/etc/passwd``, ``C:\\...``)
        * NUL bytes / other shenanigans

    We achieve this by resolving the joined path and asserting it stays
    under ``uploads_root``. This is belt-and-braces with FastAPI's path
    converter (which already strips ``/``), but covers OS-specific
    quirks (e.g. Windows alternate data streams).
    """
    uploads_root = os.path.realpath(_ensure_uploads_dir())
    candidate = os.path.realpath(os.path.join(uploads_root, filename))
    # ``commonpath`` raises on mixed drives (Windows); treat as 404.
    try:
        if os.path.commonpath([uploads_root, candidate]) != uploads_root:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found") from exc

    if not os.path.exists(candidate):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return FileResponse(candidate)


def _content_item_to_response(item: ContentItem, variants: list[PlatformVariant] | None = None) -> ContentItemResponse:
    file_size_mb = float(item.file_size_mb) if item.file_size_mb is not None else None
    pv = []
    if variants is not None:
        pv = [
            PlatformVariantResponse(
                id=v.id,
                platform=v.platform,
                caption=v.caption,
                hashtags=v.hashtags,
                metadata=v.metadata_json,
                media_url=v.media_url,
                scheduled_at=v.scheduled_at,
                published_at=v.published_at,
                status=v.status,
                error_message=v.error_message,
                retry_count=v.retry_count,
                manually_edited=v.manually_edited,
                updated_at=v.updated_at,
            )
            for v in variants
        ]

    return ContentItemResponse(
        id=item.id,
        user_id=item.user_id,
        title=item.title,
        original_file_url=item.original_file_url,
        file_type=item.file_type,
        file_size_mb=file_size_mb,
        status=item.status,
        scheduled_at=item.scheduled_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
        platform_variants=pv,
    )


@router.post("/upload", response_model=ContentItemResponse, status_code=status.HTTP_201_CREATED)
async def upload_content(
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tmp_path: str | None = None
    try:
        # User-requested: ensure local uploads/ exists from process start.
        os.makedirs("uploads", exist_ok=True)

        # P-8 layer 1: MIME allow-list. Anything outside the allow-list
        # is rejected with 415 *before* we touch disk or external APIs.
        try:
            mime = assert_mime_allowed(file.content_type)
        except UploadValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
            ) from exc

        family = family_of(mime)  # "image" or "video"
        max_bytes = max_bytes_for(mime)
        # P-8 layer 2: filename sanitization. The temp file's suffix is
        # derived from the validated MIME, NEVER from upload.filename
        # (which is fully user-controlled).
        safe_suffix = safe_extension_for(mime)

        tmp_path, size_bytes = await _save_upload_to_temp(
            file, max_bytes=max_bytes, suffix=safe_suffix
        )

        # P-8 layer 3: magic-byte sniff. Even with the right MIME header,
        # the body must look like the claimed format. This blocks
        # ``Content-Type: image/png`` masquerades carrying a Windows PE,
        # shell script, etc.
        try:
            with open(tmp_path, "rb") as _probe:
                head = _probe.read(16)
            assert_magic_matches(head, mime)
        except UploadValidationError as exc:
            logger.warning(
                "upload.magic_mismatch",
                extra={
                    "mime": mime,
                    "user_id": str(user.id),
                    # NOTE: ``filename`` is a reserved LogRecord attribute and
                    # raises ``KeyError`` if passed via ``extra=``. Use a
                    # namespaced key.
                    "upload_filename": file.filename,
                    "size_bytes": size_bytes,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file body does not match the declared content type.",
            ) from exc

        # Always create uploads/ for local-dev fallback.
        _ensure_uploads_dir()

        resource_type = "video" if family == "video" else "image"

        try:
            _require_cloudinary_config()
            result = _upload_to_cloudinary(tmp_path, resource_type=resource_type)
            secure_url = result.get("secure_url")
            public_id = result.get("public_id")
            if not secure_url or not public_id:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Cloudinary upload failed")

            item = ContentItem(
                user_id=user.id,
                title=title,
                original_file_url=str(secure_url),
                file_type=resource_type,
                file_size_mb=_mb_from_bytes(size_bytes),
                status="draft",
                cloudinary_public_id=str(public_id),
                cloudinary_resource_type=resource_type,
            )
            db.add(item)
            await db.commit()
            await db.refresh(item)
            return _content_item_to_response(item, variants=[])
        except HTTPException:
            raise
        except Exception:
            # Fallback: store file locally and serve it from backend.
            #
            # P-8: do NOT trust upload.filename for the on-disk suffix.
            # ``sanitize_extension`` accepts only ``.[a-z0-9]{1,8}`` and
            # falls back to the validated MIME's canonical extension.
            raw_suffix = ""
            if file.filename and "." in file.filename:
                raw_suffix = "." + file.filename.rsplit(".", 1)[-1]
            suffix = sanitize_extension(raw_suffix, fallback=safe_suffix)
            local_name = f"{uuid.uuid4().hex}{suffix}"
            uploads = _ensure_uploads_dir()
            local_path = os.path.join(uploads, local_name)
            os.replace(tmp_path, local_path)
            tmp_path = None

            base = str(request.base_url).rstrip("/")
            file_url = f"{base}/api/v1/content/uploads/{local_name}"

            item = ContentItem(
                user_id=user.id,
                title=title,
                original_file_url=file_url,
                file_type=resource_type,
                file_size_mb=_mb_from_bytes(size_bytes),
                status="draft",
                cloudinary_public_id=None,
                cloudinary_resource_type=None,
            )
            db.add(item)
            await db.commit()
            await db.refresh(item)
            return _content_item_to_response(item, variants=[])
    except HTTPException:
        raise
    except Exception:
        logger.exception("content upload failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Upload failed. Please try again.",
        ) from None
    finally:
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


@router.get("", response_model=dict)
async def list_content(
    status_filter: str | None = Query(default=None, alias="status"),
    platform: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    base = select(ContentItem).where(ContentItem.user_id == user.id)
    count_from = ContentItem

    if status_filter:
        base = base.where(ContentItem.status == status_filter)

    if platform:
        base = base.join(PlatformVariant).where(PlatformVariant.platform == platform)
        count_from = base.subquery()
        count_stmt = select(func.count(func.distinct(count_from.c.id)))
        total = int((await db.execute(count_stmt)).scalar_one() or 0)
    else:
        count_stmt = select(func.count(ContentItem.id)).where(ContentItem.user_id == user.id)
        if status_filter:
            count_stmt = count_stmt.where(ContentItem.status == status_filter)
        total = int((await db.execute(count_stmt)).scalar_one() or 0)

    stmt = base.order_by(ContentItem.created_at.desc()).offset((page - 1) * limit).limit(limit)
    items = (await db.execute(stmt)).scalars().unique().all()

    return {
        "page": page,
        "limit": limit,
        "total": total,
        "items": [_content_item_to_response(i, variants=[]) for i in items],
    }


@router.get("/{id}", response_model=ContentItemResponse)
async def get_content(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    cached = content_detail_cache_svc.get_cached(str(user.id), str(id))
    if cached:
        try:
            return ContentItemResponse.model_validate(cached)
        except Exception:
            pass

    item = (await db.execute(select(ContentItem).where(ContentItem.id == id, ContentItem.user_id == user.id))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found")

    variants = (
        await db.execute(
            select(PlatformVariant)
            .where(PlatformVariant.content_item_id == item.id)
            .order_by(PlatformVariant.id.asc())
        )
    ).scalars().all()

    resp = _content_item_to_response(item, variants=variants)
    try:
        content_detail_cache_svc.set_cached(str(user.id), str(id), resp.model_dump(mode="json"))
    except Exception:
        pass
    return resp


@router.patch("/{content_id}/variants/{platform}", response_model=PlatformVariantUpdateAck)
async def update_platform_variant(
    content_id: uuid.UUID,
    platform: str,
    body: PlatformVariantUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Update the generated content for a specific platform variant.
    Allows users to manually edit AI-generated captions before publishing.
    """

    VALID_PLATFORMS = {"instagram", "twitter", "linkedin", "facebook", "youtube"}

    normalized = canonical_platform_key(platform)
    if normalized not in VALID_PLATFORMS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid platform: {platform}")

    content_item = await content_repository.get_by_id_and_user(db, content_id=content_id, user_id=user.id)
    if content_item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")

    updated = await platform_variant_repository.update_variant_data(
        db,
        content_id=content_id,
        platform=normalized,
        new_data=dict(body.data or {}),
        manually_edited=True,
    )

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Variant for {platform} not found",
        )

    content_detail_cache_svc.invalidate(str(user.id), str(content_id))
    return PlatformVariantUpdateAck(status="updated", platform=normalized, content_id=str(content_id))


@router.patch("/{id}", response_model=ContentItemResponse)
async def update_content(
    id: uuid.UUID,
    payload: ContentItemUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = (await db.execute(select(ContentItem).where(ContentItem.id == id, ContentItem.user_id == user.id))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found")

    if payload.title is not None:
        item.title = payload.title
    if payload.scheduled_at is not None:
        item.scheduled_at = payload.scheduled_at

    db.add(item)
    await db.commit()
    await db.refresh(item)
    content_detail_cache_svc.invalidate(str(user.id), str(id))
    return _content_item_to_response(item, variants=[])


@router.delete("/{id}")
async def delete_content(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if settings.EVALUATION_MODE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Evaluation mode is enabled: deleting content is disabled for demo safety.",
        )

    item = (await db.execute(select(ContentItem).where(ContentItem.id == id, ContentItem.user_id == user.id))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found")

    # Attempt Cloudinary delete (best-effort)
    if item.cloudinary_public_id and item.cloudinary_resource_type:
        _require_cloudinary_config()
        try:
            cloudinary.uploader.destroy(item.cloudinary_public_id, resource_type=item.cloudinary_resource_type, invalidate=True)
        except Exception:
            # Don't block DB cleanup
            pass

    # DB delete cascades should remove platform_variants; this is extra safety.
    await db.execute(delete(PlatformVariant).where(PlatformVariant.content_item_id == item.id))
    await db.delete(item)
    await db.commit()
    content_detail_cache_svc.invalidate(str(user.id), str(id))
    return {"ok": True}


@router.post("/{id}/process")
async def process_content(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = (await db.execute(select(ContentItem).where(ContentItem.id == id, ContentItem.user_id == user.id))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found")

    try:
        if not settings.gemini_api_key.strip():
            logger.warning(
                "process_content.enqueue_without_gemini_key",
                extra={"content_id": str(id)},
            )

        item.status = "processing"
        db.add(item)
        await db.commit()
        content_detail_cache_svc.invalidate(str(user.id), str(id))

        start_content_processing_background(str(item.id), str(user.id))
        return {"message": "Processing started", "job_id": None}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to enqueue processing task")
        try:
            item.status = "failed"
            db.add(item)
            await db.commit()
        except Exception:
            logger.exception("Failed to mark content as failed after enqueue error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Processing could not be started. Please try again shortly.",
        ) from None

