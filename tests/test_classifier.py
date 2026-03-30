from __future__ import annotations

import unittest
from types import SimpleNamespace

from linuxdoscanner.ai_config import AIProviderConfig
from linuxdoscanner.classifier import TopicClassifier


class TopicClassifierNotificationPolicyTests(unittest.TestCase):
    def _build_classifier(self) -> TopicClassifier:
        settings = SimpleNamespace(
            llm_batch_size=10,
            openai_api_key=None,
            openai_base_url=None,
            openai_model=None,
        )
        return TopicClassifier(settings, ai_config=AIProviderConfig())

    def test_question_primary_label_does_not_force_notify_from_codex_secondary_label(self) -> None:
        classifier = self._build_classifier()

        analysis = classifier._normalize_llm_result(
            {
                "primary_label": "求助问答",
                "labels": ["求助问答", "Codex技巧"],
                "summary": "纯提问帖。",
                "reasons": ["没有给出做法。"],
                "requires_notification": False,
            }
        )

        self.assertFalse(analysis.requires_notification)

    def test_requires_notification_is_single_source_of_truth(self) -> None:
        classifier = self._build_classifier()

        analysis = classifier._normalize_llm_result(
            {
                "primary_label": "求助问答",
                "labels": ["求助问答", "AI相关", "Codex技巧"],
                "summary": "普通求助。",
                "reasons": ["缺少可执行经验。"],
                "requires_notification": True,
            }
        )

        self.assertTrue(analysis.requires_notification)

    def test_malformed_requires_notification_value_is_tightened_to_false(self) -> None:
        classifier = self._build_classifier()

        analysis = classifier._normalize_llm_result(
            {
                "primary_label": "Codex技巧",
                "labels": ["AI相关", "Codex技巧"],
                "summary": "给出了明确的使用技巧。",
                "reasons": ["包含可操作步骤。"],
                "requires_notification": "false",
            }
        )

        self.assertFalse(analysis.requires_notification)


if __name__ == "__main__":
    unittest.main()
