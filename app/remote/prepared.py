"""Opaque reference to a server-owned, validated company import spool."""
from dataclasses import dataclass


@dataclass
class ImportPreparation:
    reference: str

    def close(self):
        # Server expires session-owned spools; no database or key on the desktop.
        from app.remote.state import client
        connection = client()
        if connection is not None and self.reference:
            from app.remote.screen_data import release_preparation
            release_preparation(self.reference)
        self.reference = ''
