"""Backup/restore of data/*.db to a private Hugging Face Dataset repo.

Why: HF Spaces free tier has an ephemeral disk — every restart wipes data/.
On process start we pull the last snapshot down; a scheduler job pushes a
fresh snapshot every few minutes and once more on shutdown, so a restart
loses at most one interval of changes.

Safety rules learned the hard way (see code review):
- If restore *failed* in this process, uploads are refused — otherwise a
  transient HF outage at boot would let us overwrite the only surviving
  snapshot with freshly-created empty databases.
- An empty repo (first run) is not a failure: restore succeeds with 0 files
  and uploads are allowed.
- Uploads snapshot each database with the sqlite3 backup API first, so a
  mid-write file is never shipped half-torn; all files go up in ONE commit.
- Restore removes stale -journal/-wal/-shm sidecars and writes atomically,
  and runs at most once per boot (sentinel), so --reload/extra workers
  can't roll live databases back.
"""
import asyncio
import logging
import os
import sqlite3
import tempfile
from pathlib import Path

logger = logging.getLogger("emma.hf_backup")

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
RESTORE_SENTINEL = DATA_DIR / ".restored"
UPLOAD_INTERVAL_MINUTES = 10
_SIDECARS = ("-journal", "-wal", "-shm")


class HFBackup:
    def __init__(self) -> None:
        self.repo_id = os.environ.get("EMMA_HF_BACKUP_REPO", "").strip()
        self.token = os.environ.get("HF_TOKEN", "").strip()
        self._lock = asyncio.Lock()
        # True only if restore() was attempted in this process and failed.
        # Uploads are blocked while set (protects the remote snapshot).
        self._restore_failed = False

    @property
    def enabled(self) -> bool:
        return bool(self.repo_id and self.token)

    # -- restore (sync: must finish before managers open the databases) --

    def restore(self) -> None:
        if not self.enabled:
            return
        if RESTORE_SENTINEL.exists():
            # Already restored this boot (uvicorn --reload / extra worker).
            return
        try:
            from huggingface_hub import HfApi, hf_hub_download

            api = HfApi(token=self.token)
            files = api.list_repo_files(self.repo_id, repo_type="dataset")
            DATA_DIR.mkdir(exist_ok=True)
            count = 0
            for name in files:
                if not name.endswith(".db"):
                    continue
                fetched = hf_hub_download(
                    self.repo_id, name, repo_type="dataset", token=self.token
                )
                target = DATA_DIR / Path(name).name
                for suffix in _SIDECARS:
                    stale = Path(str(target) + suffix)
                    if stale.exists():
                        stale.unlink()
                tmp = target.with_suffix(".db.restoring")
                tmp.write_bytes(Path(fetched).read_bytes())
                os.replace(tmp, target)
                count += 1
            RESTORE_SENTINEL.touch()
            logger.info("Restored %d database(s) from %s", count, self.repo_id)
        except Exception as exc:  # noqa: BLE001 - a fresh start beats a crash loop
            self._restore_failed = True
            logger.error(
                "HF restore FAILED — uploads disabled this run to protect the "
                "remote snapshot. Fix and restart. Error: %s", exc
            )

    # -- upload --

    def _snapshot_and_upload(self) -> int:
        from huggingface_hub import HfApi

        api = HfApi(token=self.token)
        with tempfile.TemporaryDirectory() as tmp:
            count = 0
            for db in sorted(DATA_DIR.glob("*.db")):
                snap = Path(tmp) / db.name
                src = sqlite3.connect(db)
                try:
                    dst = sqlite3.connect(snap)
                    try:
                        src.backup(dst)
                    finally:
                        dst.close()
                finally:
                    src.close()
                count += 1
            if count:
                # One commit for all databases: keeps history compact and
                # avoids HF commit-rate limits (vs one commit per file).
                api.upload_folder(
                    folder_path=tmp,
                    repo_id=self.repo_id,
                    repo_type="dataset",
                    commit_message="emma db snapshot",
                )
        return count

    async def upload(self, force: bool = False) -> None:
        if not self.enabled:
            return
        if self._restore_failed:
            logger.warning("Skipping backup: restore failed this run (see above)")
            return
        if self._lock.locked() and not force:
            return  # previous upload still running; skip this tick
        async with self._lock:
            try:
                count = await asyncio.to_thread(self._snapshot_and_upload)
                logger.info("Backed up %d database(s) to %s", count, self.repo_id)
            except Exception as exc:  # noqa: BLE001 - backup failure must not kill the app
                logger.warning("HF backup failed (will retry next interval): %s", exc)


hf_backup = HFBackup()
