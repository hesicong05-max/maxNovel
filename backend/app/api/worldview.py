"""Worldview upload and parsing API."""

import logging
import os
import tempfile
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.core.auth import User, get_current_user, get_project_for_owner
from app.core.worldview_parser import worldview_parser
from app.database import get_db
from app.models.project import Project, ProjectStatus, Worldview
from app.schemas.models import (
    WorldviewCreate,
    WorldviewImportRequest,
    WorldviewImportResponse,
    WorldviewResponse,
)

router = APIRouter(prefix="/api/worldview", tags=["worldview"])

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".txt", ".md", ".markdown", ".doc", ".docx"}
MAX_UPLOAD_SIZE = app_settings.MAX_UPLOAD_SIZE


def _extract_text_from_docx(file_bytes: bytes) -> str:
    """Extract text from a .docx file using python-docx."""
    import io

    from docx import Document

    doc = Document(io.BytesIO(file_bytes))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    # Also extract text from tables
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                text = cell.text.strip()
                if text:
                    paragraphs.append(text)
    return "\n".join(paragraphs)


async def _extract_text_from_doc(file_bytes: bytes, filename: str) -> str:
    """
    Extract text from a .doc file.
    Uses macOS textutil to convert .doc to plain text.
    Falls back to raising an error if textutil is unavailable.
    """
    import asyncio as aio

    with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    output_path = tmp_path + ".txt"
    try:
        proc = await aio.create_subprocess_exec(
            "textutil", "-convert", "txt", "-output", output_path, tmp_path,
            stdout=aio.subprocess.PIPE,
            stderr=aio.subprocess.PIPE,
        )
        try:
            await aio.wait_for(proc.wait(), timeout=30)
        except aio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise HTTPException(status_code=422, detail="解析 .doc 文件超时，请尝试转换为 .docx 格式")

        if proc.returncode == 0 and os.path.exists(output_path):
            with open(output_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read().strip()
                if text:
                    return text

        raise HTTPException(
            status_code=422,
            detail="无法解析 .doc 文件，请尝试转换为 .docx 格式后重新上传",
        )
    finally:
        for path in [tmp_path, output_path]:
            if os.path.exists(path):
                os.unlink(path)


@router.post("/{project_id}/upload-file")
async def upload_worldview_file(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    file: UploadFile = File(...),
):
    """
    Upload a document file (.txt, .md, .doc, .docx) and extract its text content.
    Returns the extracted text for the frontend to review before calling /import.
    """
    # Verify project ownership
    await get_project_for_owner(project_id, current_user, db)

    # Validate file extension
    filename = file.filename or ""
    _, ext = os.path.splitext(filename.lower())
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {ext}，请上传 .txt / .md / .doc / .docx 文件",
        )

    # Read file content with size limit
    file_bytes = await file.read()
    file_size = len(file_bytes)
    if file_size > MAX_UPLOAD_SIZE:
        max_mb = MAX_UPLOAD_SIZE / (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"文件过大 ({file_size / 1024:.0f}KB)，最大允许 {max_mb:.0f}MB",
        )
    logger.info("File upload: %s (%d bytes)", filename, file_size)

    if file_size < 10:
        raise HTTPException(status_code=400, detail="文件内容为空或过短")

    # Extract text based on file type
    if ext in (".txt", ".md", ".markdown"):
        # Try UTF-8 first, fall back to other encodings
        for encoding in ("utf-8", "gbk", "gb2312", "big5", "latin-1"):
            try:
                text = file_bytes.decode(encoding)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            text = file_bytes.decode("utf-8", errors="replace")
    elif ext == ".docx":
        try:
            text = _extract_text_from_docx(file_bytes)
        except Exception as e:
            raise HTTPException(
                status_code=422,
                detail=f"解析 .docx 文件失败: {str(e)}",
            )
    elif ext == ".doc":
        try:
            text = await _extract_text_from_doc(file_bytes, filename)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=422,
                detail=f"解析 .doc 文件失败: {str(e)}",
            )
    else:
        raise HTTPException(status_code=400, detail=f"不支持的文件格式: {ext}")

    if not text or len(text.strip()) < 10:
        raise HTTPException(
            status_code=400,
            detail="从文件中提取的文本内容过短，至少需要 10 个字符",
        )

    return {"text": text, "filename": filename, "char_count": len(text)}


@router.post("/{project_id}/import", response_model=WorldviewImportResponse)
async def import_worldview(
    project_id: str,
    data: WorldviewImportRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """
    Import worldview from a free-form document.
    Uses LLM to extract structured worldview elements from the text.
    Does NOT save to database — returns extracted elements for user review.
    The user then saves via POST /{project_id} with the extracted data.
    """
    project = await get_project_for_owner(project_id, current_user, db)

    if not data.document_text or len(data.document_text.strip()) < 10:
        raise HTTPException(status_code=400, detail="文档内容过短，至少需要 10 个字符")

    try:
        extracted = await worldview_parser.parse_document(
            data.document_text, project.genre.value
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"解析失败: {str(e)}")

    # Count total elements
    total = (
        len(extracted.get("characters", []))
        + len(extracted.get("geography", []))
        + len(extracted.get("factions", []))
        + len(extracted.get("power_system", []))
        + len(extracted.get("history", []))
        + len(extracted.get("conflicts", []))
        + len(extracted.get("special_settings", []))
    )

    return WorldviewImportResponse(
        characters=extracted.get("characters", []),
        geography=extracted.get("geography", []),
        factions=extracted.get("factions", []),
        power_system=extracted.get("power_system", []),
        history=extracted.get("history", []),
        conflicts=extracted.get("conflicts", []),
        special_settings=extracted.get("special_settings", []),
        raw_text=data.document_text,
        source="imported",
        element_count=total,
    )


@router.post("/{project_id}", response_model=WorldviewResponse)
async def set_worldview(
    project_id: str,
    data: WorldviewCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    project = await get_project_for_owner(project_id, current_user, db)

    # Delete existing worldview if any (query directly to avoid lazy loading)
    wv_result = await db.execute(select(Worldview).where(Worldview.project_id == project_id))
    existing_wv = wv_result.scalar_one_or_none()
    if existing_wv:
        await db.delete(existing_wv)
        await db.flush()

    # Parse worldview into elements
    worldview_dict = data.model_dump()
    elements = worldview_parser.parse(worldview_dict)

    worldview = Worldview(
        project_id=project_id,
        characters=worldview_dict["characters"],
        geography=worldview_dict["geography"],
        factions=worldview_dict["factions"],
        power_system=worldview_dict["power_system"],
        history=worldview_dict["history"],
        conflicts=worldview_dict["conflicts"],
        special_settings=worldview_dict["special_settings"],
        raw_text=data.raw_text,
        source=data.source,
        parsed_elements=elements,
    )
    db.add(worldview)

    project.status = ProjectStatus.WORLDVIEW_SET
    await db.commit()
    await db.refresh(worldview)

    return WorldviewResponse(
        id=worldview.id,
        project_id=worldview.project_id,
        characters=data.characters,
        geography=data.geography,
        factions=data.factions,
        power_system=data.power_system,
        history=data.history,
        conflicts=data.conflicts,
        special_settings=data.special_settings,
        raw_text=data.raw_text,
        source=worldview.source,
        parsed_elements=elements,
        created_at=worldview.created_at,
    )


@router.get("/{project_id}", response_model=WorldviewResponse)
async def get_worldview(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)

    result = await db.execute(select(Worldview).where(Worldview.project_id == project_id))
    worldview = result.scalar_one_or_none()
    if not worldview:
        raise HTTPException(status_code=404, detail="世界观不存在，请先上传")

    return WorldviewResponse(
        id=worldview.id,
        project_id=worldview.project_id,
        characters=worldview.characters or [],
        geography=worldview.geography or [],
        factions=worldview.factions or [],
        power_system=worldview.power_system or [],
        history=worldview.history or [],
        conflicts=worldview.conflicts or [],
        special_settings=worldview.special_settings or [],
        raw_text=worldview.raw_text,
        source=worldview.source or "manual",
        parsed_elements=worldview.parsed_elements or [],
        created_at=worldview.created_at,
    )


@router.get("/{project_id}/summary")
async def get_worldview_summary(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)

    result = await db.execute(select(Worldview).where(Worldview.project_id == project_id))
    worldview = result.scalar_one_or_none()
    if not worldview:
        raise HTTPException(status_code=404, detail="世界观不存在")

    summary = worldview_parser.summary(worldview.parsed_elements or [])
    return summary
