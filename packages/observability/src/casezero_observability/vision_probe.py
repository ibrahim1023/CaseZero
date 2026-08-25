import time

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, Field, ValidationError


class VisionOutput(BaseModel):
    observation: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    source_id: str


def run_vision_probe(client: OpenAI, model: str, image: str, source_id: str) -> dict[str, object]:
    prompt = f"Return one JSON object with exactly three fields: observation must be one nonempty string describing only directly visible evidence; confidence must be a number from 0.0 to 1.0; source_id must be exactly {source_id}. Do not return an observation list."
    started = time.perf_counter()
    failure = None
    for attempt in range(1, 4):
        try:
            response = client.chat.completions.create(model=model, messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":image}}]}], response_format={"type":"json_object"})
            output = VisionOutput.model_validate_json(response.choices[0].message.content or "")
            if output.source_id != source_id:
                raise ValueError("invented source id")
            return {"status":"success","attempts":attempt,"latency":round(time.perf_counter()-started,3)}
        except ValidationError as error:
            failure = str(error.errors()[0]["type"])
            prompt += " Previous output failed validation; obey the schema exactly."
        except ValueError as error:
            failure = type(error).__name__
            prompt += " Previous output used an invalid source id; copy it exactly."
        except OpenAIError as error:
            failure = type(error).__name__
            break
    return {"status":"failure","failure_type":failure,"latency":round(time.perf_counter()-started,3)}
