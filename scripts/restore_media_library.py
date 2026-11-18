"""Rebuild Media Library records from the files already in the S3 media bucket.

After the database loss on 15 Nov 2026 the files were still in S3 but the
media_assets rows were gone, so the Media Library was empty. This lists the
bucket and adds a "ready" record for each file that has none. Names come from
the file name (the original names were lost); rename them in the CMS if needed.

Student-owned files (assignment submissions, profile photos, admission results)
are skipped: they belonged to records that no longer exist.

    python scripts/restore_media_library.py            preview only
    python scripts/restore_media_library.py --apply    add the records
"""

import argparse
import asyncio
import re
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.modules.cms.media_service import ASSIGNMENT_DOCUMENT_TYPES, PPTX_CONTENT_TYPE, _client, _public_url  # noqa: E402

SKIP_FOLDERS = {"assignment-submissions", "profile-images", "profile-photos", "admissions", "backups"}
TYPES = {
    ".mp4": ("video", "video/mp4"), ".webm": ("video", "video/webm"),
    ".jpg": ("image", "image/jpeg"), ".jpeg": ("image", "image/jpeg"), ".png": ("image", "image/png"),
    ".webp": ("image", "image/webp"), ".gif": ("image", "image/gif"),
    ".pdf": ("document", "application/pdf"), ".pptx": ("document", PPTX_CONTENT_TYPE),
    **{ext: ("document", ctype) for ext, ctype in ASSIGNMENT_DOCUMENT_TYPES.items()},
}
UPLOAD_PREFIX = re.compile(r"^[0-9a-f]{32}-")


def describe(key: str) -> dict | None:
    folder = key.split("/", 1)[0]
    extension = Path(key).suffix.lower()
    if folder in SKIP_FOLDERS or extension not in TYPES or key.endswith("/"):
        return None
    kind, content_type = TYPES[extension]
    stem = UPLOAD_PREFIX.sub("", Path(key).stem) or "asset"
    title = re.sub(r"[-_]+", " ", stem).strip().capitalize() or "Asset"
    return {"folder": folder[:80], "kind": kind, "content_type": content_type,
            "original_filename": f"{stem}{extension}"[:255], "title": title[:100]}


def list_bucket() -> list[dict]:
    objects = []
    for page in _client().get_paginator("list_objects_v2").paginate(Bucket=settings.MEDIA_BUCKET):
        objects += page.get("Contents", [])
    return objects


async def main(apply: bool) -> int:
    engine.echo = False
    objects = await asyncio.to_thread(list_bucket)
    async with AsyncSessionLocal() as db:
        known = set((await db.execute(text("SELECT object_key FROM media_assets"))).scalars())
        names = {n.lower() for n in (await db.execute(text("SELECT name FROM media_assets"))).scalars()}
        rows, counts = [], Counter()
        for obj in sorted(objects, key=lambda o: o["LastModified"]):
            info = describe(obj["Key"])
            if info is None:
                counts["skipped"] += 1
                continue
            if obj["Key"] in known:
                counts["already listed"] += 1
                continue
            name, n = info["title"], 2
            while name.lower() in names:
                name, n = f"{info['title']} ({n})", n + 1
            names.add(name.lower())
            rows.append({**info, "name": name, "key": obj["Key"], "size": obj["Size"],
                         "url": _public_url(obj["Key"]), "created": obj["LastModified"]})
            counts[f"to add: {info['folder']}"] += 1
        for label, n in sorted(counts.items()):
            print(f"  {label}: {n}")
        if not apply:
            print("Preview only; add --apply to write.")
            return 0
        for row in rows:
            await db.execute(text(
                "INSERT INTO media_assets (object_key, bucket, name, original_filename, content_type, size_bytes, kind, "
                "folder, public_url, status, created_at, updated_at) VALUES (:key, :bucket, :name, :original_filename, "
                ":content_type, :size, :kind, :folder, :url, 'ready', :created, :created) ON CONFLICT (object_key) DO NOTHING"
            ), {**row, "bucket": settings.MEDIA_BUCKET})
        await db.commit()
    print(f"Added {len(rows)} media records.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    async def run() -> int:
        try:
            return await main(args.apply)
        finally:
            await engine.dispose()

    sys.exit(asyncio.run(run()))
