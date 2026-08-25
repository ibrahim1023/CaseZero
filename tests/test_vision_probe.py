from types import SimpleNamespace

from casezero_observability.vision_probe import run_vision_probe


class Completions:
    def __init__(self, contents): self.contents=iter(contents)
    def create(self, **kwargs):
        content=next(self.contents)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class Client:
    def __init__(self, contents): self.chat=SimpleNamespace(completions=Completions(contents))


def test_probe_accepts_grounded_schema_without_storing_output() -> None:
    record=run_vision_probe(Client(['{"observation":"Visible title","confidence":0.8,"source_id":"PAGE-1"}']),"model","image","PAGE-1")
    assert record["status"]=="success"
    assert "observation" not in record


def test_probe_retries_invalid_confidence() -> None:
    client=Client(['{"observation":"Visible","confidence":80,"source_id":"PAGE-1"}','{"observation":"Visible","confidence":0.8,"source_id":"PAGE-1"}'])
    assert run_vision_probe(client,"model","image","PAGE-1")["attempts"]==2
