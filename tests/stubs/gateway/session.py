"""Stub: gateway.session.SessionSource — the identity of a message's origin."""
from gateway.config import Platform


class SessionSource:
    def __init__(self, platform=None, chat_id="", user_id="", thread_id=None):
        self.platform = platform
        self.chat_id = chat_id
        self.user_id = user_id
        self.thread_id = thread_id

    def to_dict(self):
        plat = self.platform
        return {
            "platform": getattr(plat, "value", plat),
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "thread_id": self.thread_id,
        }

    @classmethod
    def from_dict(cls, blob):
        plat = blob.get("platform")
        if isinstance(plat, str):
            plat = Platform(plat)
        return cls(platform=plat, chat_id=blob.get("chat_id", ""),
                   user_id=blob.get("user_id", ""), thread_id=blob.get("thread_id"))
