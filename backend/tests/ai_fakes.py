"""A stand-in AI provider for tests: it answers with whatever the test hands it, and never touches the network."""

from datetime import date
from typing import Any

from app.services.ai_provider import ProviderReply, Usage
from app.services.errors import AiServiceError


class FakeProvider:
    name = "fake"
    label = "Fake AI"
    supports_documents = True

    def __init__(
        self,
        plan: dict[str, Any] | None = None,
        document: dict[str, Any] | None = None,
        error: AiServiceError | None = None,
    ) -> None:
        self.plan_reply = plan if plan is not None else {"tool": None}
        self.document_reply = document if document is not None else {"rows": []}
        self.error = error
        self.calls: list[tuple[str, Any]] = []

    def plan(self, question: str, tools: list[dict[str, Any]], today: date) -> ProviderReply:
        self.calls.append(("plan", question))
        if self.error:
            raise self.error
        return ProviderReply(self.plan_reply, Usage(120, 30), "fake-model")

    def extract_document(self, image: bytes, media_type: str, kind: str) -> ProviderReply:
        self.calls.append(("document", kind))
        if self.error:
            raise self.error
        return ProviderReply(self.document_reply, Usage(900, 200), "fake-model")
