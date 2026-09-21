"""Image analysis providers (OCR / vision) behind one interface, and none of them required.

The application never depends on a provider: without one, photos can still be taken, a barcode read in the
browser is still looked up locally, and the screens say "Image analysis is not configured yet." Nothing in
the business logic knows which provider is behind the interface, so OpenAI vision, Gemini, AWS, Azure or a
local OCR can be added later by writing one class and listing it in `PROVIDERS`; no other file changes. NO
provider is bundled: adding one is a deliberate step that needs its own key (`IMAGE_ANALYSIS_API_KEY`,
backend only) and a privacy review.

Privacy rules that hold for every provider (enforced by `image_intelligence_service`, not by the provider):
  * a picture is sent to a provider ONLY when the user explicitly asks for analysis with that option, and
  the
    provider is configured, and the feature is in the plan;
  * the picture is sent for that one request and is not kept by this app because of it;
  * a provider's answer is only ever a SUGGESTION for a person to review, never a fact to save.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.core.config import Settings


class ImageProviderError(Exception):
    """The provider could not read the picture (down, slow, refused, bad reply). `reason` is safe to show."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ProductGuess:
    """What a provider thinks a product photo shows. Every field is optional and only ever a suggestion."""

    name: str | None = None
    brand: str | None = None
    pack_text: str | None = None  # "750 ml"
    category_hint: str | None = None
    barcode: str | None = None


@dataclass(frozen=True)
class ImageReading:
    text_lines: list[str] = field(default_factory=list)  # all visible text, in reading order
    product: ProductGuess | None = None


class ImageAnalysisProvider(Protocol):
    name: str
    label: str

    def is_configured(self, settings: Settings) -> bool: ...

    def analyze(self, image: bytes, content_type: str, settings: Settings) -> ImageReading: ...


# Providers that ship with the app: none. Register one here (or in tests) to make analysis available.
PROVIDERS: list[Callable[[], ImageAnalysisProvider]] = []


def all_providers() -> list[ImageAnalysisProvider]:
    return [make() for make in PROVIDERS]


def configured_provider(settings: Settings) -> ImageAnalysisProvider | None:
    """The provider the operator selected, if it exists and is fully configured. Otherwise None."""
    wanted = (settings.image_analysis_provider or "").strip().lower()
    if not wanted:
        return None
    for provider in all_providers():
        if provider.name == wanted and provider.is_configured(settings):
            return provider
    return None
