"""
Contract for delivering a "busy" or "free" message to a contact on some
messaging platform. Nothing implements a real platform yet (that's the
"Contact Automation" item still on the roadmap) - but Busy Mode is designed
against this interface now, so plugging in WhatsApp/Telegram/etc later is
one new class, same pattern as AIProvider.
"""
from abc import ABC, abstractmethod


class MessengerAdapter(ABC):
    name: str = "base"

    @abstractmethod
    async def send(self, contact_name: str, message: str) -> bool:
        """Return True if the message was sent (or queued) successfully."""
        raise NotImplementedError


class ConsoleMessenger(MessengerAdapter):
    """
    Default adapter: just logs what *would* be sent. Safe placeholder until
    a real platform (WhatsApp Business API, Telegram bot, etc.) is wired in.
    """
    name = "console"

    async def send(self, contact_name: str, message: str) -> bool:
        print(f"[Emma -> {contact_name}]: {message}")
        return True
