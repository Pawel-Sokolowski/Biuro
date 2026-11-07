"""Value returned from the mailbox badge/notification background read."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MailNotificationSnapshot:
    total: int
    last_ids: dict[int, int]
    messages: list[dict[str, Any]]
