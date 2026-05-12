from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from linuxdoscanner.ai_config import (
    AIConfigManager,
    normalize_api_base_url,
    normalize_chat_completions_url,
    normalize_models_url,
)
from linuxdoscanner.classifier import TopicClassifier
from linuxdoscanner.storage import Database


class AIBaseUrlNormalizationTests(unittest.TestCase):
    def test_keeps_api_base_url_without_adding_v1(self) -> None:
        self.assertEqual(normalize_api_base_url("https://proxy.example.com"), "https://proxy.example.com")
        self.assertEqual(normalize_api_base_url("https://api.openai.com/v1"), "https://api.openai.com/v1")

    def test_strips_endpoint_suffix_once(self) -> None:
        self.assertEqual(
            normalize_api_base_url("https://proxy.example.com/v1/models"),
            "https://proxy.example.com/v1",
        )
        self.assertEqual(
            normalize_api_base_url("https://proxy.example.com/openai/v1/chat/completions?debug=1"),
            "https://proxy.example.com/openai/v1",
        )

    def test_endpoint_builders_use_the_same_api_base(self) -> None:
        base_url = "https://proxy.example.com/openai/v1"

        self.assertEqual(normalize_models_url(base_url), "https://proxy.example.com/openai/v1/models")
        self.assertEqual(
            normalize_chat_completions_url(base_url),
            "https://proxy.example.com/openai/v1/chat/completions",
        )

    def test_save_config_persists_normalized_api_base_url(self) -> None:
        settings = SimpleNamespace(openai_base_url="", openai_api_key="", openai_model="")
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "linuxdo.sqlite3")
            database.initialize()
            manager = AIConfigManager(settings, database)

            config = manager.save_config({"base_url": "https://proxy.example.com/v1/models?x=1"})

            self.assertEqual(config.base_url, "https://proxy.example.com/v1")
            self.assertEqual(database.get_app_config_json("ai_config")["base_url"], "https://proxy.example.com/v1")

    def test_classifier_uses_the_shared_endpoint_builder(self) -> None:
        settings = SimpleNamespace(
            llm_batch_size=10,
            openai_api_key="sk-test",
            openai_base_url="https://proxy.example.com/openai/v1",
            openai_model="test-model",
        )

        classifier = TopicClassifier(settings)
        try:
            self.assertEqual(classifier._llm_base_url, "https://proxy.example.com/openai/v1")
            self.assertEqual(
                classifier._llm_chat_url,
                "https://proxy.example.com/openai/v1/chat/completions",
            )
        finally:
            if classifier._llm_http is not None:
                classifier._llm_http.close()


if __name__ == "__main__":
    unittest.main()
