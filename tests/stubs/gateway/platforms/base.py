"""Stub: gateway.platforms.base — the inbound seam the hook patches."""
import enum


class MessageType(enum.Enum):
    TEXT = "text"
    COMMAND = "command"


class MessageEvent:
    def __init__(self, text="", source=None, message_id=None,
                 media_urls=None, media_types=None):
        self.text = text
        self.source = source
        self.message_id = message_id
        self.media_urls = list(media_urls or [])
        self.media_types = list(media_types or [])
        self.auto_skill = None


class BasePlatformAdapter:
    """Only handle_message matters: it is the seam install_patches replaces."""

    async def handle_message(self, event):
        return None
