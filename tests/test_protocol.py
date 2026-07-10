import gzip
import json
import unittest

import protocol


class TestProtocolErrorResponse(unittest.TestCase):
    def test_server_error_response_has_message_type(self):
        payload = gzip.compress(
            json.dumps({"message": "invalid request"}).encode("utf-8")
        )
        packet = bytearray(
            protocol.generate_header(
                message_type=protocol.SERVER_ERROR_RESPONSE,
                message_type_specific_flags=protocol.NO_SEQUENCE,
            )
        )
        packet.extend((45000001).to_bytes(4, "big"))
        packet.extend(len(payload).to_bytes(4, "big"))
        packet.extend(payload)

        response = protocol.parse_response(bytes(packet))

        self.assertEqual(response["message_type"], "SERVER_ERROR")
        self.assertEqual(response["code"], 45000001)
        self.assertEqual(response["payload_msg"]["message"], "invalid request")


if __name__ == "__main__":
    unittest.main()
