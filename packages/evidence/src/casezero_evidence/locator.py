from dataclasses import dataclass
from typing import Protocol

from casezero_evidence.models import SourceLocator, TextLocator


class LocatorResolutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedRegion:
    media_type: str
    content: bytes


class LocatorAdapter(Protocol):
    def supports(self, locator: object) -> bool: ...

    def resolve(self, source: bytes, locator: object) -> ResolvedRegion: ...


class TextLocatorAdapter:
    def supports(self, locator: object) -> bool:
        return isinstance(locator, TextLocator)

    def resolve(self, source: bytes, locator: object) -> ResolvedRegion:
        if not isinstance(locator, TextLocator):
            raise LocatorResolutionError("text adapter requires TextLocator")
        try:
            text = source.decode("utf-8")
        except UnicodeDecodeError as error:
            raise LocatorResolutionError("text source is not UTF-8") from error
        if locator.end > len(text):
            raise LocatorResolutionError("text locator exceeds source length")
        return ResolvedRegion(
            media_type="text/plain; charset=utf-8",
            content=text[locator.start : locator.end].encode(),
        )


class LocatorResolver:
    def __init__(self, adapters: tuple[LocatorAdapter, ...]) -> None:
        self._adapters = adapters

    def resolve(self, source: bytes, locator: SourceLocator) -> ResolvedRegion:
        matches = [adapter for adapter in self._adapters if adapter.supports(locator)]
        if not matches:
            raise LocatorResolutionError("no locator adapter supports this locator")
        if len(matches) > 1:
            raise LocatorResolutionError("multiple locator adapters support this locator")
        return matches[0].resolve(source, locator)
