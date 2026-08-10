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

    def test_scene_one_contains_fixed_script_lines(self):
        robot_lines = [
            step["text"]
            for step in config.EDUCATION_DEMO_SCRIPT
            if step.get("type") == "say"
        ]
        self.assertEqual(
            robot_lines,
            [
                "同学们好，我是你们本次项目制学习的机器人导师。今天我们要完成一个真实的智能体设计任务：为大一新生设计一个校园学习生活规划 Agent。",
                "本项目分为三个阶段：第一，明确用户和需求；第二，设计 Agent 的输入、决策和交互方式；第三，完成方案展示和评审。",
                "评审时，我会重点关注四点：用户是否具体、需求是否真实、架构是否清晰、方案是否能够持续优化。",
                "我注意到这位刚才在评分规则这里停留了比较久，好像有些疑惑。你是不是想确认，‘需求是否真实’这一项具体怎么判断？",
                "功能多不一定代表项目好。这个项目更看重的是：用户对象是否明确，使用场景是否具体，问题是否真实存在。也就是说，你们后面设计功能时，要先说明这些功能为什么需要，而不是一开始就堆很多功能。",
            ],
        )

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

    def test_consecutive_student_pauses_wait_as_one_discussion_turn(self):
        events = []
        session = ScriptSession(
            events=events,
            scripted_steps=[
                {"type": "pause", "speaker": "学生 A", "text": "第一个回答"},
                {"type": "pause", "speaker": "学生 B", "text": "第二个回答"},
                {"type": "pause", "speaker": "学生 C", "text": "第三个回答"},
                {"type": "say", "text": "机器人总结"},
            ],
            external_stop_event=None,
            is_running=True,
            receive_error=None,
            client=FakeClient(events),
            dialog_write_queue=FakeQueue(),
        )

        asyncio.run(dialog_session.DialogSession.play_scripted_demo(session))

        self.assertEqual(
            [event for event in events if event[0] == "wait_user"],
            [
                (
                    "wait_user",
                    "学生 A、学生 B、学生 C",
                    config.EDUCATION_DEMO_RESPONSE_DELAY_SEC,
                )
            ],
        )
        self.assertEqual(
            session.dialog_write_queue.items,
            ["学生 A: 第一个回答", "学生 B: 第二个回答", "学生 C: 第三个回答", "机器人: 机器人总结"],
        )

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
