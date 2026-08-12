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


class OpeningLineTest(unittest.TestCase):
    def test_prompt_does_not_repeat_any_opening_line(self):
        prompt = config.build_expert_robot_system_prompt()

        self.assertIn("say_hello 已经完成", prompt)
        for opening in config.EXPERT_ROBOT_OPENING_LINES:
            self.assertNotIn(opening, prompt)

    def test_say_hello_uses_one_of_five_opening_lines(self):
        async def run_test():
            client = RealtimeDialogClient({}, "session-id")
            client.ws = FakeWebSocket()
            client.voice_udp_socket.close()
            client.voice_udp_socket = mock.Mock()
            with mock.patch(
                "realtime_dialog_client.random.randrange", return_value=3
            ):
                await client.say_hello()

            request = client.ws.sent[0]
            offset = len(protocol.generate_header()) + 4
            session_id_size = int.from_bytes(request[offset : offset + 4], "big")
            offset += 4 + session_id_size
            payload_size = int.from_bytes(request[offset : offset + 4], "big")
            offset += 4
            payload = json.loads(gzip.decompress(request[offset : offset + payload_size]))
            self.assertEqual(payload["content"], config.EXPERT_ROBOT_OPENING_LINES[3])
            self.assertEqual(len(config.EXPERT_ROBOT_OPENING_LINES), 5)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
