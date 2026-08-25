from casezero_evidence import DocumentType

from casezero_ingestion.media import DetectedMediaType
from casezero_ingestion.processors import Processor


class UnsupportedMediaError(LookupError):
    pass


class AmbiguousProcessorError(LookupError):
    pass


class ProcessorRegistry:
    def __init__(self, processors: tuple[Processor, ...]) -> None:
        self._processors = processors

    def select(
        self, media_type: DetectedMediaType, document_type: DocumentType
    ) -> Processor:
        matches = [
            processor
            for processor in self._processors
            if processor.supports(media_type, document_type)
        ]
        if not matches:
            raise UnsupportedMediaError(
                f"no processor supports {media_type.value}/{document_type.value}"
            )
        if len(matches) > 1:
            names = ", ".join(processor.name for processor in matches)
            raise AmbiguousProcessorError(f"multiple processors support media: {names}")
        return matches[0]
