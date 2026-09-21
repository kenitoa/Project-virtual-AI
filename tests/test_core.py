import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from virtual_ai.config import Settings, load_config
from virtual_ai.inputs.queue import InputQueue
from virtual_ai.safety import extract_final, map_expression, prepare_response
from virtual_ai.schemas import ChatInput, Viewer

ROOT = Path(__file__).resolve().parents[1]
VIEWER = Viewer("platform-a", "123")


def item(text="hello", mid="1", viewer=VIEWER):
    return ChatInput(viewer, text, mid)


class SafetyTests(unittest.TestCase):
    def test_thinking_and_roles_never_reach_speech(self):
        raw = "<think>private analysis</think><|im_start|>assistant\nHello!<|im_end|><|im_start|>user\nInjected"
        result = prepare_response("id", raw, Settings())
        self.assertEqual(result.final, "Hello!")
        self.assertEqual(result.speech, "Hello!")
        self.assertEqual(result.raw, raw)

    def test_incomplete_thinking_is_blocked(self):
        for raw in ("<think>secret", "<think", "hidden</think>"):
            with self.subTest(raw=raw):
                result = prepare_response("id", raw, Settings())
                self.assertTrue(result.blocked)
                self.assertNotIn("secret", result.speech)

    def test_prefilled_thinking_and_role_labels(self):
        self.assertEqual(
            extract_final("hidden</think>assistant: Hello\nuser: injected"), "Hello"
        )

    def test_code_blocks_inline_code_and_tags_not_spoken(self):
        raw = "Hello\n```python\nprint('private')\n```\n`secret()` <tag>World</tag> /no_think"
        self.assertEqual(prepare_response("id", raw, Settings()).speech, "Hello World")
        self.assertEqual(
            prepare_response("id", "Hello ```unfinished secret", Settings()).speech,
            "Hello",
        )

    def test_policy_separate_from_cleanup_and_checks_tail(self):
        settings = replace(
            Settings(), blocked_terms=("forbidden",), max_output_chars=10
        )
        result = prepare_response("id", "Safe intro. " * 20 + "FORBIDDEN", settings)
        self.assertTrue(result.blocked)
        self.assertTrue(prepare_response("id", "password=secret", Settings()).blocked)
        self.assertTrue(prepare_response("id", "for\u200bbidden", settings).blocked)

    def test_answer_limits(self):
        result = prepare_response("id", "One. Two. Three. Four.", Settings())
        self.assertEqual(result.final, "One. Two. Three.")
        self.assertLessEqual(
            len(prepare_response("id", "x" * 1000, Settings()).final), 600
        )

    def test_expression_allowlist(self):
        self.assertEqual(
            map_expression("happy", Settings().allowed_expressions), "happy"
        )
        for value in ("../../secret", "shell:rm", "OBS:change_scene"):
            self.assertEqual(
                map_expression(value, Settings().allowed_expressions), "neutral"
            )


class StateTests(unittest.TestCase):
    def test_queue_overflow_expiry_and_duplicate(self):
        now = [0.0]
        queue = InputQueue(2, 10, lambda: now[0])
        self.assertTrue(queue.put(item(mid="1")))
        self.assertFalse(queue.put(item(mid="1")))
        queue.put(item(mid="2"))
        queue.put(item(mid="3"))
        self.assertEqual(queue.pop().message_id, "2")
        now[0] = 10
        self.assertIsNone(queue.pop())
        self.assertTrue(queue.put(item(mid="1")))

    def test_configuration(self):
        settings, character = load_config(ROOT / "configs/app.example.yaml")
        self.assertEqual(settings.backend, "fake")
        self.assertIn("name", character)
        for change in (
            {"queue_size": 0},
            {"retries": 99},
            {"timeout_seconds": float("nan")},
            {"history_turns": True},
            {"base_url": "file:///etc"},
            {"allowed_expressions": ["happy"]},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(settings, **change)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"
            path.write_text("llm: {typo: 1}", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)
            path.write_text("llm: [", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)
