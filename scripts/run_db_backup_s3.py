"""Encrypted database backup to S3, keeping only the latest copy.

Runs pg_dump (custom format) of the public schema, checks the dump with
pg_restore --list, encrypts it with AES-256-GCM and uploads it to
DB_BACKUP_S3_KEY in MEDIA_BUCKET. The upload replaces the previous backup, and
only happens after the new dump has passed the check, so a failed run leaves the
last good backup in place.

The media bucket is public through CloudFront, so the file is never uploaded
unencrypted. The key comes from DB_BACKUP_ENCRYPTION_KEY; keep a copy offline.

The systemd timer starts this every night. It skips the run when the backup in
S3 is newer than DB_BACKUP_MIN_INTERVAL_HOURS (36), so a backup is taken every
second night, and a failed night is retried the next night.

    python scripts/run_db_backup_s3.py                 backup if due
    python scripts/run_db_backup_s3.py --force         backup now
    python scripts/run_db_backup_s3.py --status        show the backup in S3
    python scripts/run_db_backup_s3.py --download OUT.dump
                                                       download + decrypt to a
                                                       pg_restore-ready file
    python scripts/run_db_backup_s3.py --decrypt IN.enc OUT.dump

Restore: pg_restore --no-owner --no-privileges -d <database url> OUT.dump
"""

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.modules.cms.media_service import _client  # noqa: E402

MAGIC = b"INSPIRE-DBBACKUP-1\n"
CHUNK = 1024 * 1024


def log(message: str) -> None:
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z {message}", flush=True)


def derive_key(salt: bytes) -> bytes:
    secret = settings.DB_BACKUP_ENCRYPTION_KEY
    if len(secret) < 24:
        raise SystemExit("DB_BACKUP_ENCRYPTION_KEY is missing or shorter than 24 characters")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(secret.encode())


def encrypt_file(src: Path, dst: Path) -> None:
    salt, nonce = os.urandom(16), os.urandom(12)
    enc = Cipher(algorithms.AES(derive_key(salt)), modes.GCM(nonce)).encryptor()
    enc.authenticate_additional_data(MAGIC)
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        fout.write(MAGIC + salt + nonce)
        while chunk := fin.read(CHUNK):
            fout.write(enc.update(chunk))
        fout.write(enc.finalize() + enc.tag)


def decrypt_file(src: Path, dst: Path) -> None:
    size = src.stat().st_size
    with open(src, "rb") as fin:
        if fin.read(len(MAGIC)) != MAGIC:
            raise SystemExit("Not an Inspire database backup file")
        salt, nonce = fin.read(16), fin.read(12)
        body_len = size - len(MAGIC) - 28 - 16
        fin.seek(size - 16)
        tag = fin.read(16)
        fin.seek(len(MAGIC) + 28)
        dec = Cipher(algorithms.AES(derive_key(salt)), modes.GCM(nonce, tag)).decryptor()
        dec.authenticate_additional_data(MAGIC)
        tmp = dst.with_name(dst.name + ".partial")
        with open(tmp, "wb") as fout:
            remaining = body_len
            while remaining > 0:
                chunk = fin.read(min(CHUNK, remaining))
                remaining -= len(chunk)
                fout.write(dec.update(chunk))
            try:
                fout.write(dec.finalize())
            except Exception:
                fout.close()
                tmp.unlink(missing_ok=True)
                raise SystemExit("Decryption failed: wrong key or the file is damaged")
    tmp.replace(dst)


def pg_env() -> dict:
    env = dict(os.environ)
    env.update(PGPASSWORD=settings.POSTGRES_PASSWORD, PGSSLMODE="disable" if settings.POSTGRES_HOST in {"localhost", "127.0.0.1"} else "require")
    return env


def dump_database(out: Path) -> int:
    cmd = [settings.PG_DUMP_PATH, "-h", settings.POSTGRES_HOST, "-p", str(settings.POSTGRES_PORT),
           "-U", settings.POSTGRES_USER, "-d", settings.POSTGRES_DB, "-n", "public",
           "--no-owner", "--no-privileges", "-Fc", "-f", str(out)]
    result = subprocess.run(cmd, env=pg_env(), capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        raise SystemExit(f"pg_dump failed: {result.stderr.strip()[-2000:]}")
    listing = subprocess.run([settings.PG_RESTORE_PATH, "-l", str(out)], capture_output=True, text=True, timeout=600)
    if listing.returncode != 0:
        raise SystemExit(f"pg_restore could not read the dump: {listing.stderr.strip()[-2000:]}")
    tables = len(re.findall(r" TABLE DATA ", listing.stdout))
    if tables == 0:
        raise SystemExit("The dump contains no table data; not uploading")
    return tables


def current_backup():
    try:
        return _client().head_object(Bucket=settings.MEDIA_BUCKET, Key=settings.DB_BACKUP_S3_KEY)
    except Exception as exc:
        if getattr(exc, "response", {}).get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def run_backup(force: bool) -> None:
    head = current_backup()
    if head and not force:
        age = datetime.now(timezone.utc) - head["LastModified"]
        if age < timedelta(hours=settings.DB_BACKUP_MIN_INTERVAL_HOURS):
            log(f"Skipped: last backup is {age.total_seconds() / 3600:.1f} h old (taken every {settings.DB_BACKUP_MIN_INTERVAL_HOURS}+ h)")
            return
    derive_key(b"\0" * 16)  # fail early if the key is not set
    with tempfile.TemporaryDirectory(prefix="inspire-dbbackup-") as tmpdir:
        plain, enc = Path(tmpdir) / "inspire-db.dump", Path(tmpdir) / "inspire-db.dump.enc"
        log("Dumping database")
        tables = dump_database(plain)
        encrypt_file(plain, enc)
        check = Path(tmpdir) / "check.dump"
        decrypt_file(enc, check)
        if sha256(check) != sha256(plain):
            raise SystemExit("Encrypted copy did not decrypt back to the dump; not uploading")
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        log(f"Uploading {enc.stat().st_size:,} bytes ({tables} tables) to s3://{settings.MEDIA_BUCKET}/{settings.DB_BACKUP_S3_KEY}")
        _client().upload_file(str(enc), settings.MEDIA_BUCKET, settings.DB_BACKUP_S3_KEY, ExtraArgs={
            "ContentType": "application/octet-stream",
            "CacheControl": "no-store",
            "Metadata": {"created-at": created, "tables": str(tables), "dump-sha256": sha256(plain)},
        })
    head = current_backup()
    if not head or head["ContentLength"] == 0:
        raise SystemExit("Upload could not be confirmed")
    log(f"Backup done: {tables} tables, replaced the previous backup")


def show_status() -> None:
    head = current_backup()
    if not head:
        print("No backup in S3 yet")
        return
    meta = head.get("Metadata", {})
    print(f"s3://{settings.MEDIA_BUCKET}/{settings.DB_BACKUP_S3_KEY}")
    print(f"  uploaded {head['LastModified']:%Y-%m-%d %H:%M} UTC, {head['ContentLength']:,} bytes, {meta.get('tables', '?')} tables")


def download(out: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="inspire-dbbackup-") as tmpdir:
        enc = Path(tmpdir) / "download.enc"
        _client().download_file(settings.MEDIA_BUCKET, settings.DB_BACKUP_S3_KEY, str(enc))
        decrypt_file(enc, out)
    print(f"Saved {out} - restore with: pg_restore --no-owner --no-privileges -d <database url> {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--force", action="store_true", help="back up now even if the last backup is recent")
    group.add_argument("--status", action="store_true", help="show the backup currently in S3")
    group.add_argument("--download", metavar="OUT", type=Path, help="download and decrypt the S3 backup")
    group.add_argument("--decrypt", nargs=2, metavar=("IN", "OUT"), type=Path, help="decrypt a downloaded .enc file")
    args = parser.parse_args()
    if not settings.MEDIA_BUCKET:
        raise SystemExit("MEDIA_BUCKET is not set")
    if args.status:
        show_status()
    elif args.download:
        download(args.download)
    elif args.decrypt:
        decrypt_file(*args.decrypt)
        print(f"Saved {args.decrypt[1]}")
    else:
        run_backup(args.force)


if __name__ == "__main__":
    main()
