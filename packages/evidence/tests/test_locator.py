import pytest
from casezero_evidence import PdfLocator, TextLocator
from casezero_evidence.locator import (
    LocatorResolutionError,
    LocatorResolver,
    ResolvedRegion,
    TextLocatorAdapter,
)


class PdfFixtureAdapter:
    def supports(self, locator: object) -> bool:
        return isinstance(locator, PdfLocator)

    def resolve(self, source: bytes, locator: object) -> ResolvedRegion:
        if not isinstance(locator, PdfLocator) or locator.reading_order != 0:
            raise LocatorResolutionError("fixture PDF location does not exist")
        return ResolvedRegion(media_type="text/plain", content=b"first paragraph")


def test_text_locator_resolves_exact_utf8_source_span() -> None:
    source = b"prefix evidence suffix"
    resolver = LocatorResolver((TextLocatorAdapter(),))

    resolved = resolver.resolve(source, TextLocator(start=7, end=15))

    assert resolved.content == b"evidence"


def test_resolver_delegates_modality_specific_locator() -> None:
    resolver = LocatorResolver((TextLocatorAdapter(), PdfFixtureAdapter()))

    resolved = resolver.resolve(b"synthetic PDF bytes", PdfLocator(page=1, reading_order=0))

    assert resolved.content == b"first paragraph"


def test_resolver_rejects_missing_or_ambiguous_adapter() -> None:
    with pytest.raises(LocatorResolutionError, match="no locator adapter"):
        LocatorResolver((TextLocatorAdapter(),)).resolve(b"pdf", PdfLocator(page=1))

    with pytest.raises(LocatorResolutionError, match="multiple locator adapters"):
        LocatorResolver((PdfFixtureAdapter(), PdfFixtureAdapter())).resolve(
            b"pdf", PdfLocator(page=1, reading_order=0)
        )
