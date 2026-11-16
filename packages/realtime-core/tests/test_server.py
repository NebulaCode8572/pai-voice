import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import server


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, raw):
        self.messages.append(json.loads(raw))


class BrowserReplyTests(unittest.IsolatedAsyncioTestCase):
    async def wait_for_pending_reply(self, call):
        for _ in range(100):
            if call.pending_replies:
                return next(iter(call.pending_replies.items()))
            await asyncio.sleep(0)
        self.fail("answer_turn did not register a pending browser reply")

    async def test_browser_reply_is_returned_for_tts(self):
        call = server.Call()
        call.ws = FakeWebSocket()

        with (
            patch.object(server, "ADAPTER_URL", ""),
            patch.object(server, "transcribe", new=AsyncMock(return_value="你好")),
            patch.object(server, "synthesize", new=AsyncMock(return_value=None)),
            patch.object(server, "request_reply", new=AsyncMock(side_effect=AssertionError("adapter should not be called"))),
        ):
            task = asyncio.create_task(server.answer_turn(None, call, None, b"audio"))
            turn_id, future = await self.wait_for_pending_reply(call)
            future.set_result("你好，我在听。")
            await task

        types = [message.get("type") for message in call.ws.messages]
        self.assertIn("transcript", types)
        self.assertIn("reply_text", types)
        self.assertIn("generation_end", types)
        reply = next(message for message in call.ws.messages if message.get("type") == "reply_text")
        self.assertEqual(reply["turn_id"], turn_id)
        self.assertEqual(reply["text"], "你好，我在听。")
        self.assertNotIn(turn_id, call.pending_replies)

    async def test_interrupted_turn_does_not_send_stale_reply(self):
        call = server.Call()
        call.ws = FakeWebSocket()

        with (
            patch.object(server, "ADAPTER_URL", ""),
            patch.object(server, "transcribe", new=AsyncMock(return_value="第一句话")),
            patch.object(server, "synthesize", new=AsyncMock(return_value=None)),
        ):
            task = asyncio.create_task(server.answer_turn(None, call, None, b"audio"))
            turn_id, future = await self.wait_for_pending_reply(call)
            call.interrupts += 1
            future.cancel()
            await task

        types = [message.get("type") for message in call.ws.messages]
        self.assertIn("transcript", types)
        self.assertNotIn("reply_text", types)
        self.assertNotIn(turn_id, call.pending_replies)


if __name__ == "__main__":
    unittest.main()
