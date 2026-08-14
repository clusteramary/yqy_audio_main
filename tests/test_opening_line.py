import asyncio
import gzip
import json
import unittest
from unittest import mock

import config
import protocol
from realtime_dialog_client import RealtimeDialogClient


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(data)


class InterviewPromptTest(unittest.TestCase):
    def test_prompt_does_not_repeat_any_opening_line(self):
        """prompt 中只含问题清单，开场白由 say_hello 单独发送，二者不能重复。"""
        plan = config.build_interview_plan()
        prompt = config.build_expert_robot_system_prompt(plan)

        for opening in config.EXPERT_ROBOT_OPENING_LINES:
            self.assertNotIn(opening, prompt)

        # 新结构应有的章节
        for section in ("身份问题", "关键问题", "次要问题", "结束语", "应答规则"):
            self.assertIn(section, prompt)

    def test_plan_picks_all_questions_locally(self):
        """所有随机项都在本地 plan 里选好，且都来自对应的问题池。"""
        plan = config.build_interview_plan()

        self.assertIn(plan["opening"]["text"], config.EXPERT_ROBOT_OPENING_LINES)
        self.assertIn(
            plan["identity"]["question"],
            [q["question"] for q in config.EXPERT_ROBOT_IDENTITY_QUESTIONS],
        )
        self.assertEqual(
            len(plan["secondary"]), config.EXPERT_ROBOT_SECONDARY_PICK_COUNT
        )
        all_secondary = [
            q["question"] for q in config.EXPERT_ROBOT_SECONDARY_QUESTIONS
        ]
        for q in plan["secondary"]:
            self.assertIn(q["question"], all_secondary)
        self.assertIn(plan["closing"]["text"], config.EXPERT_ROBOT_CLOSING_LINES)

    def test_prompt_contains_only_picked_secondary_questions(self):
        """prompt 只包含本地选中的2个次要问题，不含问题池里其他题目。"""
        plan = config.build_interview_plan()
        prompt = config.build_expert_robot_system_prompt(plan)

        picked_texts = {q["question"] for q in plan["secondary"]}
        for q in config.EXPERT_ROBOT_SECONDARY_QUESTIONS:
            if q["question"] in picked_texts:
                self.assertIn(q["question"], prompt)
            else:
                self.assertNotIn(q["question"], prompt)

    def test_secondary_rotation_does_not_repeat_next_pick(self):
        """轮转：下一次选中的2个问题与本次不同（题目池数量 > 每次选取数时成立）。"""
        first_ids = {q["id"] for q in config._pick_secondary_questions()}
        second_ids = {q["id"] for q in config._pick_secondary_questions()}
        self.assertEqual(len(first_ids), config.EXPERT_ROBOT_SECONDARY_PICK_COUNT)
        self.assertEqual(len(second_ids), config.EXPERT_ROBOT_SECONDARY_PICK_COUNT)
        self.assertTrue(first_ids.isdisjoint(second_ids))

    def test_say_hello_uses_plan_opening(self):
        """say_hello 必须使用本地选定的开场白（而不是自己再随机一次）。"""

        async def run_test():
            client = RealtimeDialogClient({}, "session-id")
            client.ws = FakeWebSocket()
            client.voice_udp_socket.close()
            client.voice_udp_socket = mock.Mock()

            plan = config.build_interview_plan()
            plan["opening"] = {
                "index": 3,
                "text": config.EXPERT_ROBOT_OPENING_LINES[3],
            }
            old_plan = getattr(config, "ACTIVE_INTERVIEW_PLAN", None)
            config.ACTIVE_INTERVIEW_PLAN = plan
            try:
                await client.say_hello()
            finally:
                config.ACTIVE_INTERVIEW_PLAN = old_plan

            request = client.ws.sent[0]
            offset = len(protocol.generate_header()) + 4
            session_id_size = int.from_bytes(request[offset : offset + 4], "big")
            offset += 4 + session_id_size
            payload_size = int.from_bytes(request[offset : offset + 4], "big")
            offset += 4
            payload = json.loads(
                gzip.decompress(request[offset : offset + payload_size])
            )
            self.assertEqual(payload["content"], config.EXPERT_ROBOT_OPENING_LINES[3])
            self.assertEqual(len(config.EXPERT_ROBOT_OPENING_LINES), 4)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
