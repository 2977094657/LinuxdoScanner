from __future__ import annotations

import unittest
from types import SimpleNamespace

from linuxdoscanner.ai_config import AIProviderConfig
from linuxdoscanner.classifier import TopicClassifier
from linuxdoscanner.models import TopicPayload


class TopicClassifierNotificationPolicyTests(unittest.TestCase):
    def _build_classifier(self) -> TopicClassifier:
        settings = SimpleNamespace(
            llm_batch_size=10,
            openai_api_key=None,
            openai_base_url=None,
            openai_model=None,
        )
        return TopicClassifier(settings, ai_config=AIProviderConfig())

    def _build_payload(self, topic_id: int) -> TopicPayload:
        return TopicPayload(
            topic_id=topic_id,
            slug=f"topic-{topic_id}",
            title=f"Topic {topic_id}",
            url=f"https://linux.do/t/topic/{topic_id}",
            first_post_html=f"<p>Topic {topic_id}</p>",
            content_text=f"Topic {topic_id}",
        )

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

    def test_default_prompts_relax_benefit_posts_without_full_steps(self) -> None:
        config = AIProviderConfig()

        self.assertIn("不必把完整领取步骤当成唯一条件", config.focus_prompt)
        self.assertIn("即使没有完整领取步骤，也应明显倾向通知", config.notification_prompt)
        self.assertIn("标题明确写福利的帖子通常不是标题党", config.notification_prompt)

    def test_legacy_default_prompts_are_migrated_to_new_defaults(self) -> None:
        config = AIProviderConfig.from_dict(
            {
                "focus_prompt": (
                    "优先识别高密度、可执行、对 AI 使用和判断有直接价值的主题。"
                    "当前重点关注 AI 前沿新闻与模型更新、实验复现、辟谣实测、Codex 或 Claude Code 使用技巧，"
                    "以及有明确路径或价值点的福利、公益站、资源信息。"
                    "正文证据必须优先于标题；注册成功庆祝、闲聊、女装整活、树洞、感情和生活水贴不要误判。"
                ),
                "notification_prompt": (
                    "只有当主题具备较强时效性、信息密度和行动价值时，才标记为需要通知。"
                    "AI 相关新闻、严谨实验或辟谣实测、Codex/Claude Code 使用技巧，应明显倾向通知；"
                    "正文直接给出福利路径、公益站入口、API 地址、密钥、额度、模型范围或具体使用方式的资源贴，也应明显倾向通知；"
                    "注册庆祝、闲聊、女装整活、感情贴、树洞和纯吐槽不要通知。"
                    "最终是否推送只看 requires_notification 这一个字段，拿不准时宁可标 false。"
                ),
            }
        )

        self.assertIn("不必把完整领取步骤当成唯一条件", config.focus_prompt)
        self.assertIn("标题明确写福利的帖子通常不是标题党", config.notification_prompt)

    def test_build_user_content_treats_clear_benefit_titles_as_actionable_signals(self) -> None:
        classifier = self._build_classifier()
        payload = self._build_payload(400)
        payload.title = "公益站 400 亿 token 福利，codex 分组 0.25 折优惠"
        payload.content_text = "福利信息帖，提及公益站 400 亿 token 福利、codex 分组 0.25 折优惠、cdk.linux.do 平台。"

        user_content = classifier._build_user_content(
            payload={
                "focus_keywords": classifier.ai_config.focus_keywords,
                "focus_prompt": classifier.ai_config.focus_prompt,
                "notification_prompt": classifier.ai_config.notification_prompt,
                "batch_size": 1,
                "documents": [classifier._build_prompt_payload(payload)],
            }
        )

        self.assertIn("不必强求完整领取步骤", user_content)
        self.assertIn("标题、摘要或正文清楚指出可领取福利", user_content)
        self.assertIn("cdk.linux.do 平台", user_content)

    def test_analyze_many_detailed_marks_unavailable_requests_as_non_retryable(self) -> None:
        classifier = self._build_classifier()
        events: list[dict[str, object]] = []

        result = classifier.analyze_many_detailed([self._build_payload(1)], progress_callback=events.append)[0]

        self.assertFalse(result.request_succeeded)
        self.assertFalse(result.should_retry)
        self.assertEqual(result.analysis.provider, "llm_unavailable")
        self.assertEqual([event["event"] for event in events], ["unavailable"])

    def test_llm_batch_detailed_marks_missing_topic_result_as_retryable(self) -> None:
        classifier = self._build_classifier()
        classifier.model_name = "test-model"
        classifier._llm_chat_url = "https://example.com/v1/chat/completions"
        classifier._llm_http = object()
        classifier._request_llm_content = lambda request_payload: (
            '[{"topic_id": 1, "primary_label": "Codex技巧", "labels": ["AI相关", "Codex技巧"], '
            '"summary": "给出了明确做法。", "reasons": ["包含可执行步骤。"], '
            '"requires_notification": true}]'
        )

        results = classifier._llm_analyze_batch_detailed(
            [self._build_payload(1), self._build_payload(2)]
        )

        self.assertTrue(results[0].request_succeeded)
        self.assertFalse(results[0].should_retry)
        self.assertFalse(results[1].request_succeeded)
        self.assertTrue(results[1].should_retry)
        self.assertEqual(results[1].analysis.provider, "llm_incomplete:test-model")

    def test_analyze_many_detailed_emits_batch_progress_events(self) -> None:
        classifier = self._build_classifier()
        classifier.model_name = "test-model"
        classifier._llm_chat_url = "https://example.com/v1/chat/completions"
        classifier._llm_http = object()
        classifier._request_llm_content = lambda request_payload: (
            '[{"topic_id": 1, "primary_label": "Codex技巧", "labels": ["AI相关", "Codex技巧"], '
            '"summary": "给出了明确做法。", "reasons": ["包含可执行步骤。"], '
            '"requires_notification": true}, '
            '{"topic_id": 2, "primary_label": "前沿快讯", "labels": ["AI相关", "前沿快讯"], '
            '"summary": "提供了有效更新。", "reasons": ["包含清晰结论。"], '
            '"requires_notification": true}]'
        )
        events: list[dict[str, object]] = []

        classifier.analyze_many_detailed(
            [self._build_payload(1), self._build_payload(2)],
            progress_callback=events.append,
        )

        self.assertEqual(
            [event["event"] for event in events],
            ["batch_start", "batch_complete"],
        )
        self.assertEqual(events[0]["completed_topics"], 0)
        self.assertEqual(events[1]["completed_topics"], 2)


if __name__ == "__main__":
    unittest.main()
