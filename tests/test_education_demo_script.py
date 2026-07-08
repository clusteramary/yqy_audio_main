import asyncio
import unittest
from types import SimpleNamespace

import config
import dialog_session
from audio_constants import ACTION_INDEX_BY_KEYWORD


class FakeClient:
    def __init__(self, events):
        self.events = events

    async def chat_tts_text(self, is_user_querying, start, end, content):
        self.events.append(("tts", start, end, content))


class FakeQueue:
    def __init__(self):
        self.items = []

    def put_nowait(self, item):
        self.items.append(item)


class ScriptSession(SimpleNamespace):
    def publish_action_keyword(self, keyword):
        self.events.append(("action", keyword))
        return True

    async def _wait_for_tts_idle(self):
        self.events.append(("wait_tts",))

    async def _sleep_or_stop(self, seconds):
        self.events.append(("sleep", seconds))


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
        self.assertIn("项目制学习的机器人导师", robot_lines[0])
        self.assertTrue(any("需求是否真实" in line for line in robot_lines))
        self.assertTrue(any("功能多不一定代表项目好" in line for line in robot_lines))

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
            client=FakeClient(events),
            dialog_write_queue=FakeQueue(),
        )

        asyncio.run(dialog_session.DialogSession.play_scripted_demo(session))

        self.assertEqual(events[0], ("action", "wave"))
        self.assertEqual(events[1], ("tts", True, True, "第一句"))
        self.assertEqual(events[2], ("wait_tts",))
        self.assertEqual(events[3], ("action", "nod"))
        self.assertIn(("sleep", 0.0), events)


if __name__ == "__main__":
    unittest.main()
