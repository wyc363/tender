import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from docling_postprocess.vlm_client import call_page_structure, extract_usage
from docling_postprocess.vlm_config import ParserVLMSettings


def settings():
    return ParserVLMSettings("https://workspace.cn-beijing.example/v1", "secret", "qwen3.7-flash",
                             0, 1000, False, 120, 1, 1572864, 1)


class FakeLiteLLM:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.kwargs = response, error, None

    def completion(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


class SequenceLiteLLM:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class VLMClientTests(unittest.TestCase):
    def test_normal_call_and_usage(self):
        body = {"page_no": 1, "reading_order": [], "classifications": [],
                "page_type": "blank", "confidence": 1.0}
        response = {"choices": [{"message": {"content": json.dumps(body)}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        fake = FakeLiteLLM(response)
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "page.jpg"; image.write_bytes(b"jpeg")
            result = call_page_structure(settings(), image, "prompt", fake)
        self.assertEqual(result.usage["input_tokens"], 10)
        self.assertEqual(result.usage["output_tokens"], 5)
        self.assertEqual(fake.kwargs["model"], "openai/qwen3.7-flash")
        self.assertEqual(fake.kwargs["extra_body"], {"enable_thinking": False})
        self.assertEqual(fake.kwargs["response_format"]["type"], "json_schema")
        image_value = fake.kwargs["messages"][1]["content"][1]["image_url"]
        self.assertEqual(image_value["max_pixels"], 1572864)

    def test_api_exception_is_raised(self):
        fake = FakeLiteLLM(error=RuntimeError("network down"))
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "page.jpg"; image.write_bytes(b"jpeg")
            with self.assertRaisesRegex(RuntimeError, "network down"):
                call_page_structure(settings(), image, "prompt", fake)

    def test_usage_missing_is_not_fabricated(self):
        self.assertEqual(extract_usage({}), {"prompt_tokens": None, "input_tokens": None,
                                             "completion_tokens": None, "output_tokens": None,
                                             "total_tokens": None})

    def test_json_schema_compatibility_fallback_is_audited(self):
        response = {"choices": [{"message": {"content": "{}"}}], "usage": {}}
        fake = SequenceLiteLLM([RuntimeError("response_format json_schema unsupported"), response])
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "page.jpg"; image.write_bytes(b"jpeg")
            result = call_page_structure(settings(), image, "prompt", fake)
        self.assertEqual(result.structured_output_mode, "json_object_local_strict_validation")
        self.assertEqual(fake.calls[1]["response_format"], {"type": "json_object"})
        self.assertIn("json_schema_to_json_object_after_compatibility_error",
                      result.compatibility_fallbacks)


if __name__ == "__main__":
    unittest.main()
