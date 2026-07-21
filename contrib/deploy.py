#!/usr/bin/env python3
"""Push Emma's code and databases to Hugging Face.

Usage:
    python contrib/deploy.py

Requires in .env: HF_TOKEN, EMMA_HF_BACKUP_REPO
Pushes to both the Space repo and the Dataset repo with HfApi.upload_folder.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import huggingface_hub as hf

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
HF_SPACE_DIR = REPO_ROOT / "contrib" / "hf-space"


def main():
    token = os.environ.get("HF_TOKEN")
    backup_repo = os.environ.get("EMMA_HF_BACKUP_REPO")
    if not token or not backup_repo:
        print("! HF_TOKEN and EMMA_HF_BACKUP_REPO must be set in .env")
        sys.exit(1)

    username = backup_repo.split("/")[0]
    space_repo = os.environ.get("EMMA_SPACE_REPO", f"{username}/emma")
    dataset_repo = backup_repo

    api = hf.HfApi(token=token)

    # ---- 1. Push code to Space repo ----
    print(f"→ Pushing code to Space: {space_repo}")

    # Stage Space files in a temporary directory
    import shutil

    tmp = REPO_ROOT / "__deploy_tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()

    entries = [
        HF_SPACE_DIR / "app.py",
        HF_SPACE_DIR / "README.md",
        HF_SPACE_DIR / "requirements.txt",
        REPO_ROOT / "main.py",
        REPO_ROOT / "config.py",
        REPO_ROOT / "api",
        REPO_ROOT / "core",
        REPO_ROOT / "web",
    ]

    for src in entries:
        dst = tmp / src.name
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True, ignore=lambda d, files: {f for f in files if f.endswith(('.pyc', '.pyo')) or f == '__pycache__' or f.startswith('.')})
        else:
            shutil.copy2(src, dst)

    api.upload_folder(
        folder_path=str(tmp),
        repo_id=space_repo,
        repo_type="space",
        commit_message="deploy emma",
        delete_patterns=["*"],
    )
    print("  ✓ Code pushed")

    # Clean up tmp
    shutil.rmtree(tmp)

    # ---- 2. Seed the Dataset with current databases ----
    print(f"→ Seeding Dataset: {dataset_repo}")
    db_dir = REPO_ROOT / "__deploy_db"
    db_dir.mkdir(exist_ok=True)

    # sqlite3 backup each db so we ship consistent snapshots
    import sqlite3

    for db in sorted(DATA_DIR.glob("*.db")):
        snap = db_dir / db.name
        src = sqlite3.connect(str(db))
        try:
            dst = sqlite3.connect(str(snap))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

    api.upload_folder(
        folder_path=str(db_dir),
        repo_id=dataset_repo,
        repo_type="dataset",
        commit_message="initial db seed",
    )
    print("  ✓ Databases seeded")

    for f in db_dir.iterdir():
        f.unlink()
    db_dir.rmdir()

    print("\n✅ Deploy complete! Check the Space build logs:")
    print(f"   https://huggingface.co/spaces/{space_repo}/logs")


if __name__ == "__main__":
    main()
