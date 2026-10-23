"""Render uploaded PPTX files as cached PDF previews for the lesson viewer."""
import asyncio
from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
from urllib.parse import unquote, urlparse
from zipfile import BadZipFile, ZipFile

from sqlalchemy import or_, select

from app.core.config import settings
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.modules.cms import media_service
from app.modules.cms.models import MediaAsset

MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_PREVIEW_BYTES = 100 * 1024 * 1024
_conversion_slots = asyncio.Semaphore(2)


def preview_key(object_key: str) -> str:
    return f"{object_key}.preview.pdf"


def validate_pptx(data: bytes) -> None:
    if not data or len(data) > MAX_SOURCE_BYTES:
        raise ValidationError("PowerPoint files must be 50 MB or smaller")
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            if not {"[Content_Types].xml", "ppt/presentation.xml"}.issubset(names):
                raise ValidationError("Choose a valid PPTX presentation")
            if len(entries) > 10000 or sum(entry.file_size for entry in entries) > 250 * 1024 * 1024:
                raise ValidationError("This presentation is too large to preview")
            if any("vbaproject" in name.lower() for name in names):
                raise ValidationError("Save the presentation as a PPTX without macros before uploading")
    except BadZipFile as exc:
        raise ValidationError("Choose a valid PPTX presentation") from exc


def _libreoffice_path() -> str | None:
    if settings.LIBREOFFICE_PATH:
        return settings.LIBREOFFICE_PATH
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if executable:
        return executable
    if os.name == "nt":
        for folder in ("ProgramFiles", "ProgramFiles(x86)"):
            candidate = Path(os.environ.get(folder, "C:/Program Files")) / "LibreOffice/program/soffice.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def convert_pptx_to_pdf(data: bytes) -> bytes:
    validate_pptx(data)
    with tempfile.TemporaryDirectory(prefix="inspire-slides-") as directory:
        root = Path(directory)
        source = root / "slides.pptx"
        output = root / "slides.pdf"
        source.write_bytes(data)
        executable = _libreoffice_path()
        env = os.environ.copy()
        if executable:
            command = [executable, f"-env:UserInstallation={(root / 'profile').as_uri()}",
                       "--headless", "--nologo", "--nodefault", "--norestore",
                       "--convert-to", "pdf:impress_pdf_Export", "--outdir", str(root), str(source)]
        elif os.name == "nt":
            # Local Windows development can use installed PowerPoint without opening a window.
            env.update(INSPIRE_SLIDES_SOURCE=str(source), INSPIRE_SLIDES_OUTPUT=str(output))
            script = """
$ErrorActionPreference = 'Stop'
$application = $null
$presentation = $null
try {
    $application = New-Object -ComObject PowerPoint.Application
    $application.AutomationSecurity = 3
    $application.DisplayAlerts = 1
    $presentation = $application.Presentations.Open($env:INSPIRE_SLIDES_SOURCE, -1, 0, 0)
    $presentation.SaveAs($env:INSPIRE_SLIDES_OUTPUT, 32)
} finally {
    if ($presentation) { $presentation.Close(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($presentation) }
    if ($application) { $application.Quit(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($application) }
}
"""
            command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]
        else:
            raise ValidationError("PowerPoint previews are unavailable. Ask your LMS administrator to enable presentation conversion.")
        try:
            process = subprocess.run(command, cwd=root, env=env, capture_output=True, timeout=120,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except subprocess.TimeoutExpired as exc:
            raise ValidationError("The presentation took too long to process. Try a smaller PPTX file.") from exc
        except OSError as exc:
            raise ValidationError("PowerPoint preview conversion is unavailable on the API server") from exc
        if process.returncode or not output.is_file():
            raise ValidationError("The presentation could not be previewed. Save it as a PPTX again and retry.")
        preview = output.read_bytes()
        if not preview.startswith(b"%PDF-") or len(preview) > MAX_PREVIEW_BYTES:
            raise ValidationError("The presentation did not produce a valid slide preview")
        return preview


def _read_object(client, bucket: str, key: str, limit: int) -> bytes:
    response = client.get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    try:
        data = body.read(limit + 1)
    finally:
        body.close()
    if len(data) > limit:
        raise ValidationError("The course material is too large to preview")
    return data


def _cached_preview(client, bucket: str, key: str) -> bytes | None:
    try:
        data = _read_object(client, bucket, key, MAX_PREVIEW_BYTES)
        if data.startswith(b"%PDF-"):
            return data
        raise ValidationError("The stored presentation preview is invalid")
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code in {"NoSuchKey", "404", "NotFound"}:
            return None
        raise


async def ensure_presentation_preview(asset) -> bytes:
    bucket, key = asset.bucket, asset.object_key
    client = media_service._client()
    async with _conversion_slots:
        cached = await asyncio.to_thread(_cached_preview, client, bucket, preview_key(key))
        if cached is not None:
            return cached
        source = await asyncio.to_thread(_read_object, client, bucket, key, MAX_SOURCE_BYTES)
        preview = await asyncio.to_thread(convert_pptx_to_pdf, source)
        await asyncio.to_thread(client.put_object, Bucket=bucket, Key=preview_key(key), Body=preview,
                               ContentType="application/pdf", ContentDisposition="inline")
        return preview


async def read_presentation_preview(db, resource_url: str) -> bytes:
    path = unquote(urlparse(resource_url).path).lstrip("/")
    candidates = [path, path.partition("/")[2]]
    asset = (await db.execute(select(MediaAsset).where(
        MediaAsset.folder == "course-materials", MediaAsset.status == "ready",
        MediaAsset.content_type == media_service.PPTX_CONTENT_TYPE,
        or_(MediaAsset.public_url == resource_url, MediaAsset.object_key.in_(candidates)),
    ))).scalars().first()
    if asset is None or resource_url not in {asset.public_url, media_service._public_url(asset.object_key)}:
        raise NotFoundError("PowerPoint material not found")
    source = SimpleNamespace(bucket=asset.bucket, object_key=asset.object_key)
    await db.commit()
    return await ensure_presentation_preview(source)


async def complete_course_upload(db, asset_id: int, user_id: int):
    asset = await db.get(MediaAsset, asset_id)
    if asset is None:
        raise NotFoundError("Course material upload not found")
    if asset.created_by != user_id:
        raise ForbiddenError("This upload does not belong to your account")
    response = await media_service.complete_upload(db, asset_id)
    if asset.folder == "course-materials" and asset.content_type == media_service.PPTX_CONTENT_TYPE:
        source = SimpleNamespace(bucket=asset.bucket, object_key=asset.object_key)
        await db.commit()
        await ensure_presentation_preview(source)
    return response
