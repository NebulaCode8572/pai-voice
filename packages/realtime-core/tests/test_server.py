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


class ActiveCallLimitTests(unittest.TestCase):
    def setUp(self):
        self.original_calls = server.CALLS.copy()
        server.CALLS.clear()

    def tearDown(self):
        server.CALLS.clear()
        server.CALLS.update(self.original_calls)

    def test_only_one_active_call_per_account(self):
        call = server.Call()
        call.user_id = "account-123"
        server.CALLS[call.id] = call

        self.assertTrue(server.has_active_call_for_user("account-123"))
        self.assertFalse(server.has_active_call_for_user("another-account"))
        self.assertFalse(server.has_active_call_for_user(""))

        call.ended = True
        self.assertFalse(server.has_active_call_for_user("account-123"))


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

    async def test_call_ended_during_transcription_does_not_register_reply(self):
        call = server.Call()
        call.ws = FakeWebSocket()
        transcription_started = asyncio.Event()
        release_transcription = asyncio.Event()

        async def slow_transcribe(_http, _pcm):
            transcription_started.set()
            await release_transcription.wait()
            return "这句话不应进入已结束的通话"

        with patch.object(server, "transcribe", new=slow_transcribe):
            task = asyncio.create_task(server.answer_turn(None, call, None, b"audio"))
            await transcription_started.wait()
            call.ended = True
            release_transcription.set()
            await task

        self.assertEqual(call.pending_replies, {})
        self.assertFalse(any(message.get("type") == "transcript" for message in call.ws.messages))

    async def test_proactive_turn_requests_app_reply_and_plays_it(self):
        call = server.Call()
        call.ws = FakeWebSocket()

        with (
            patch.object(server, "ADAPTER_URL", ""),
            patch.object(server, "synthesize", new=AsyncMock(return_value=None)),
            patch.object(server, "request_reply", new=AsyncMock(side_effect=AssertionError("adapter should not be called"))),
        ):
            task = asyncio.create_task(server.proactive_turn(None, call, None))
            turn_id, future = await self.wait_for_pending_reply(call)
            self.assertTrue(any(message.get("type") == "proactive_turn" for message in call.ws.messages))
            future.set_result("早上好，该起床啦。")
            await task

        reply = next(message for message in call.ws.messages if message.get("type") == "reply_text")
        self.assertEqual(reply["turn_id"], turn_id)
        self.assertEqual(reply["text"], "早上好，该起床啦。")
        self.assertNotIn(turn_id, call.pending_replies)

    async def test_interrupted_proactive_turn_does_not_send_stale_reply(self):
        call = server.Call()
        call.ws = FakeWebSocket()

        with (
            patch.object(server, "ADAPTER_URL", ""),
            patch.object(server, "synthesize", new=AsyncMock(return_value=None)),
        ):
            task = asyncio.create_task(server.proactive_turn(None, call, None))
            turn_id, future = await self.wait_for_pending_reply(call)
            call.interrupts += 1
            future.cancel()
            await task

        types = [message.get("type") for message in call.ws.messages]
        self.assertIn("proactive_turn", types)
        self.assertNotIn("reply_text", types)
        self.assertNotIn(turn_id, call.pending_replies)

    async def test_browser_reply_timeout_notifies_the_client(self):
        call = server.Call()
        call.ws = FakeWebSocket()

        with (
            patch.object(server, "ADAPTER_URL", ""),
            patch.object(server, "transcribe", new=AsyncMock(return_value="你好")),
            patch.object(server.asyncio, "wait_for", new=AsyncMock(side_effect=asyncio.TimeoutError)),
        ):
            await server.answer_turn(None, call, None, b"audio")

        timeout_event = next(
            message for message in call.ws.messages
            if message.get("type") == "reply_timeout"
        )
        self.assertTrue(timeout_event["turn_id"])
        self.assertTrue(any(message.get("type") == "error" for message in call.ws.messages))
        self.assertEqual(call.pending_replies, {})

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


class FakeAsrResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self):
        return {"text": "识别结果", "duration": 1.2}

    async def text(self):
        return ""


class FakeAsrHttp:
    def __init__(self):
        self.url = None
        self.kwargs = None

    def post(self, url, **kwargs):
        self.url = url
        self.kwargs = kwargs
        return FakeAsrResponse()


class MiniMaxAsrTests(unittest.IsolatedAsyncioTestCase):
    async def test_minimax_asr_uploads_wav_with_server_side_key(self):
        http = FakeAsrHttp()
        with (
            patch.object(server, "ASR_PROVIDER", "minimax"),
            patch.object(server, "MINIMAX_KEY", "server-secret"),
            patch.object(server, "MINIMAX_BASE_URL", "https://api.minimaxi.com/v1"),
            patch.object(server, "MINIMAX_ASR_MODEL", "asr-1.0"),
        ):
            result = await server.transcribe(http, b"\\x00\\x00" * 160)

        self.assertEqual(result, "识别结果")
        self.assertEqual(http.url, "https://api.minimaxi.com/v1/speech_to_text")
        self.assertEqual(http.kwargs["headers"]["Authorization"], "Bearer server-secret")
        self.assertEqual(http.kwargs["data"]._fields[1][0].get("name"), "model")
        self.assertEqual(http.kwargs["data"]._fields[1][2], "asr-1.0")
        self.assertEqual(http.kwargs["data"]._fields[2][2], "json")

    async def test_minimax_asr_requires_server_side_key(self):
        with (
            patch.object(server, "ASR_PROVIDER", "minimax"),
            patch.object(server, "MINIMAX_KEY", ""),
        ):
            with self.assertRaisesRegex(RuntimeError, "API Key 未配置"):
                await server.transcribe(FakeAsrHttp(), b"\\x00\\x00" * 160)


class MiniMaxTtsConfigTests(unittest.TestCase):
    def test_call_voice_settings_are_sanitized_and_secret_fields_ignored(self):
        config = server.normalize_call_tts_config({
            "model": "speech-2.8-hd",
            "voice_id": "char_voice_01",
            "speed": 1.25,
            "pitch": 2.6,
            "language_boost": "Chinese",
            "api_key": "must-not-be-accepted",
            "base_url": "https://attacker.example",
        })
        self.assertEqual(config, {
            "model": "speech-2.8-hd",
            "voice_id": "char_voice_01",
            "speed": 1.25,
            "pitch": 3,
            "language_boost": "Chinese",
        })
        self.assertNotIn("api_key", config)
        self.assertNotIn("base_url", config)

    def test_voice_settings_match_float_defaults_and_selected_character(self):
        config = server.normalize_call_tts_config({
            "model": "speech-2.8-hd",
            "voice_id": "char_voice_01",
            "speed": 1.4,
            "pitch": -2,
            "language_boost": "Chinese",
        })
        with (
            patch.object(server, "MINIMAX_MODEL", "speech-01-turbo"),
            patch.object(server, "MINIMAX_VOICE", "fallback_voice"),
            patch.object(server, "MINIMAX_SPEED", 1.0),
            patch.object(server, "MINIMAX_PITCH", 0),
            patch.object(server, "MINIMAX_LANGUAGE_BOOST", ""),
            patch.object(server, "MINIMAX_EMOTION", ""),
        ):
            body = server.minimax_request_body("[语音条: 你好。]", stream=False, tts_config=config)

        self.assertEqual(body["model"], "speech-2.8-hd")
        self.assertEqual(body["text"], "你好。")
        self.assertEqual(body["voice_setting"], {
            "voice_id": "char_voice_01",
            "speed": 1.4,
            "vol": 1.0,
            "pitch": -2,
        })
        self.assertEqual(body["audio_setting"], {
            "sample_rate": 44_100,
            "format": "mp3",
            "channel": 1,
            "bitrate": 256_000,
        })
        self.assertEqual(body["language_boost"], "Chinese")

    def test_streaming_uses_pcm_chunks_and_non_streaming_keeps_float_mp3_format(self):
        with (
            patch.object(server, "MINIMAX_VOICE", "fallback_voice"),
            patch.object(server, "MINIMAX_MODEL", "speech-01-turbo"),
            patch.object(server, "MINIMAX_EMOTION", ""),
            patch.object(server, "MINIMAX_LANGUAGE_BOOST", ""),
        ):
            streaming = server.minimax_request_body("你好", stream=True)
            buffered = server.minimax_request_body("你好", stream=False)

        self.assertEqual(streaming["audio_setting"]["format"], "pcm")
        self.assertEqual(streaming["audio_setting"]["sample_rate"], 44_100)
        self.assertEqual(streaming["stream_options"], {"exclude_aggregated_audio": True})
        self.assertNotIn("bitrate", streaming["audio_setting"])
        self.assertEqual(buffered["audio_setting"]["format"], "mp3")
        self.assertEqual(buffered["audio_setting"]["bitrate"], 256_000)


if __name__ == "__main__":
    unittest.main()
