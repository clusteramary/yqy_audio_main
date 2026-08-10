import asyncio
import unittest
from types import SimpleNamespace

import config
import dialog_session
from audio_constants import ACTION_INDEX_BY_KEYWORD


class FakeClient:
    def __init__(self, events):
        self.events = events

    async def chat_text_query(self, content):
        self.events.append(("query", content))


class FakeQueue:
    def __init__(self):
        self.items = []

    def put_nowait(self, item):
        self.items.append(item)


class FakeInt32:
    def __init__(self, data=0):
        self.data = data


class FakePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, msg):
        self.messages.append(msg)


class ScriptSession(SimpleNamespace):
    def publish_action_keyword(self, keyword):
        self.events.append(("action", keyword))
        return True

    async def _wait_for_tts_idle(self):
        self.events.append(("wait_tts",))
        return True

    def _raise_receive_error(self):
        if getattr(self, "receive_error", None) is not None:
            raise self.receive_error

    def _build_scripted_line_query(self, text):
        return dialog_session.DialogSession._build_scripted_line_query(text)

    async def _sleep_or_stop(self, seconds):
        self.events.append(("sleep", seconds))

    async def _wait_for_script_user_turn(self, speaker, response_delay):
        self.events.append(("wait_user", speaker, response_delay))
        return True


class TestEducationDemoScript(unittest.TestCase):
    def test_all_script_actions_are_mapped(self):
        missing = []
        for step in config.EDUCATION_DEMO_SCRIPT:
            for field in ("actions_before", "actions_after"):
                for keyword in step.get(field, []) or []:
                    if keyword not in ACTION_INDEX_BY_KEYWORD:
                        missing.append(keyword)
        self.assertEqual(missing, [])

    def test_scene_three_contains_new_fixed_script_lines(self):
        robot_lines = [
            step["text"]
            for step in config.EDUCATION_DEMO_SCRIPT
            if step.get("type") == "say"
        ]
        self.assertEqual(
            robot_lines,
            [
                "本轮项目讨论已完成总结。小组最初的问题范围较宽，经过两次引导后，已经聚焦为大一新生考试周规划 Agent。",
                "从过程表现看，",
                "姜同学在需求理解方面贡献较多，",
                "何同学在系统决策设计方面表现突出，",
                "杨同学提出了反馈调整机制。建议教师后续重点关注小组对数据来源和评价指标的完善。",
            ],
        )

    def test_scene_three_has_delays_before_student_profile_names(self):
        preface = next(
            step
            for step in config.EDUCATION_DEMO_SCRIPT
            if step.get("text") == "从过程表现看，"
        )
        profile_steps = [
            step
            for step in config.EDUCATION_DEMO_SCRIPT
            if step.get("type") == "say"
            and any(name in step.get("text", "") for name in ("姜同学", "何同学", "杨同学"))
        ]

        self.assertEqual(
            [step["text"] for step in profile_steps],
            [
                "姜同学在需求理解方面贡献较多，",
                "何同学在系统决策设计方面表现突出，",
                "杨同学提出了反馈调整机制。建议教师后续重点关注小组对数据来源和评价指标的完善。",
            ],
        )
        self.assertEqual(
            [step["wait_after"] for step in profile_steps[:2]], [0.5, 0.5]
        )
        self.assertEqual(preface["wait_after"], 0.5)
        self.assertEqual(preface["actions_before"], ["right_front"])
        self.assertEqual(profile_steps[-1]["actions_after"], ["good"])

    def test_script_player_order(self):
        events = []
        session = ScriptSession(
            events=events,
            scripted_steps=[
                {
                    "type": "say",
                    "text": "第一句",
                    "actions_before": ["wave"],
                    "actions_after": ["nod"],
                    "wait_after": 0.0,
                },
                {
                    "type": "pause",
                    "speaker": "学生 A",
                    "text": "学生台词",
                    "duration": 0.0,
                },
            ],
            external_stop_event=None,
            is_running=True,
            receive_error=None,
            client=FakeClient(events),
            dialog_write_queue=FakeQueue(),
        )

        asyncio.run(dialog_session.DialogSession.play_scripted_demo(session))

        self.assertEqual(events[0], ("action", "wave"))
        self.assertEqual(events[1][0], "query")
        self.assertIn("第一句", events[1][1])
        self.assertEqual(events[2], ("wait_tts",))
        self.assertEqual(events[3], ("action", "nod"))
        self.assertIn(("sleep", 0.0), events)
        self.assertIn(("wait_user", "学生 A", 0.0), events)

    def test_scripted_mode_ignores_content_keyword_detection(self):
        old_int32 = dialog_session.Int32
        dialog_session.Int32 = FakeInt32
        try:
            session = SimpleNamespace(
                is_scripted_demo=True,
                is_sending_chat_tts_text=False,
                action_index_pub=FakePublisher(),
                action_index_topic="/action_index",
            )
            dialog_session.DialogSession.handle_server_response(
                session,
                {
                    "message_type": "SERVER_FULL_RESPONSE",
                    "event": 999,
                    "payload_msg": {"content": "请看右手边。"},
                },
            )
            self.assertEqual(session.action_index_pub.messages, [])
        finally:
            dialog_session.Int32 = old_int32

    def test_script_stops_when_tts_does_not_start(self):
        events = []
        session = ScriptSession(
            events=events,
            scripted_steps=[
                {"type": "say", "text": "第一句"},
                {"type": "say", "text": "第二句", "actions_before": ["nod"]},
            ],
            external_stop_event=None,
            is_running=True,
            receive_error=None,
            client=FakeClient(events),
            dialog_write_queue=FakeQueue(),
        )

        async def tts_timeout():
            events.append(("wait_tts",))
            return False

        session._wait_for_tts_idle = tts_timeout

        with self.assertRaisesRegex(RuntimeError, "固定台词未能正常播放"):
            asyncio.run(dialog_session.DialogSession.play_scripted_demo(session))

        self.assertNotIn(("action", "nod"), events)
        self.assertEqual(sum(event[0] == "query" for event in events), 1)

    def test_missing_message_type_is_ignored(self):
        dialog_session.DialogSession.handle_server_response(
            SimpleNamespace(), {"code": 1234, "payload_msg": "bad response"}
        )

    def test_pause_adds_reaction_delay_before_robot_reply(self):
        events = []
        session = ScriptSession(
            events=events,
            scripted_steps=[
                {"type": "pause", "speaker": "学生 A", "text": "回答"},
                {"type": "say", "text": "机器人回应"},
            ],
            external_stop_event=None,
            is_running=True,
            receive_error=None,
            client=FakeClient(events),
            dialog_write_queue=FakeQueue(),
        )

        asyncio.run(dialog_session.DialogSession.play_scripted_demo(session))

        self.assertIn(
            ("wait_user", "学生 A", config.EDUCATION_DEMO_RESPONSE_DELAY_SEC),
            events,
        )


if __name__ == "__main__":
    unittest.main()
