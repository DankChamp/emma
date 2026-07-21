"""Persistence helpers for ephemeral hosts (Hugging Face Spaces).

Mirrors data/*.db into a private HF Dataset repo so state survives Space
restarts. Inert unless both EMMA_HF_BACKUP_REPO and HF_TOKEN are set, so
normal local runs are unaffected.
"""

from .hf_backup import hf_backup

__all__ = ["hf_backup"]
