from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import bridge


def make_config(root: Path) -> bridge.Config:
    return bridge.Config(
        token="test-token",
        allowed_chat_ids={"123"},
        codex_bin="codex",
        codex_workdir=root,
        codex_sandbox="workspace-write",
        codex_resume_session="thread-id",
        codex_timeout_seconds=30,
        state_path=root / "state.json",
        runtime_path=None,
        require_codex_prefix=False,
        inbox_enabled=False,
        inbox_path=root / "inbox.md",
        inbox_jsonl_path=root / "inbox.jsonl",
        persona_enabled=False,
        persona_path=root / "persona.md",
        memory_enabled=False,
        memory_auto_enabled=False,
        memory_jsonl_path=root / "memory.jsonl",
        memory_recent_events=0,
        memory_max_chars=0,
        ack_message="",
        attachments_enabled=True,
        max_download_bytes=20 * 1024 * 1024,
        max_upload_bytes=20 * 1024 * 1024,
        max_artifact_files=4,
        audio_transcribe_command=None,
        audio_transcribe_timeout_seconds=180,
        audio_transcript_max_chars=12000,
        tts_mode="off",
        tts_command=None,
        tts_timeout_seconds=120,
        tts_max_chars=1200,
        tts_output_extension=".mp3",
        tts_send_as="audio",
    )


class IncomingAttachmentTests(unittest.TestCase):
    def test_selects_largest_telegram_photo(self) -> None:
        attachment = bridge.incoming_attachment_for(
            {
                "message_id": 7,
                "photo": [
                    {"file_id": "small", "file_size": 100, "width": 100, "height": 100},
                    {"file_id": "large", "file_size": 500, "width": 500, "height": 500},
                ],
            }
        )

        self.assertIsNotNone(attachment)
        assert attachment is not None
        self.assertEqual(attachment.file_id, "large")
        self.assertEqual(attachment.kind, "image")
        self.assertEqual(attachment.filename, "telegram-photo-7.jpg")

    def test_accepts_markdown_pdf_and_image_documents(self) -> None:
        cases = [
            ("notes.md", "text/markdown", "markdown"),
            ("paper.pdf", "application/pdf", "pdf"),
            ("diagram.png", "image/png", "image"),
        ]
        for filename, mime_type, expected_kind in cases:
            with self.subTest(filename=filename):
                attachment = bridge.incoming_attachment_for(
                    {
                        "message_id": 9,
                        "document": {
                            "file_id": "file",
                            "file_name": filename,
                            "mime_type": mime_type,
                            "file_size": 123,
                        },
                    }
                )
                self.assertIsNotNone(attachment)
                assert attachment is not None
                self.assertEqual(attachment.kind, expected_kind)

    def test_rejects_unsupported_document(self) -> None:
        attachment = bridge.incoming_attachment_for(
            {
                "document": {
                    "file_id": "file",
                    "file_name": "archive.zip",
                    "mime_type": "application/zip",
                }
            }
        )
        self.assertIsNone(attachment)

    def test_normalizes_supported_mime_type_to_safe_extension(self) -> None:
        attachment = bridge.incoming_attachment_for(
            {
                "message_id": 11,
                "document": {
                    "file_id": "file",
                    "file_name": "upload.bin",
                    "mime_type": "application/pdf",
                },
            }
        )
        self.assertIsNotNone(attachment)
        assert attachment is not None
        self.assertEqual(attachment.filename, "upload.pdf")

    def test_accepts_voice_message_as_audio_attachment(self) -> None:
        attachment = bridge.incoming_attachment_for(
            {
                "message_id": 42,
                "voice": {
                    "file_id": "voice-id",
                    "mime_type": "audio/ogg",
                    "file_size": 321,
                },
            }
        )

        self.assertIsNotNone(attachment)
        assert attachment is not None
        self.assertEqual(attachment.kind, "audio")
        self.assertEqual(attachment.filename, "telegram-voice-42.oga")
        self.assertEqual(attachment.mime_type, "audio/ogg")

    def test_accepts_audio_message_as_audio_attachment(self) -> None:
        attachment = bridge.incoming_attachment_for(
            {
                "message_id": 43,
                "audio": {
                    "file_id": "audio-id",
                    "file_name": "Voice memo",
                    "mime_type": "audio/mp4",
                    "file_size": 654,
                },
            }
        )

        self.assertIsNotNone(attachment)
        assert attachment is not None
        self.assertEqual(attachment.kind, "audio")
        self.assertEqual(attachment.filename, "Voice memo.m4a")


class ArtifactTests(unittest.TestCase):
    def test_extracts_attachment_directive(self) -> None:
        visible, requests = bridge.extract_attachment_directive(
            'Done.\n<telegram_attachments>{"files":[{"path":"result.png","type":"photo","caption":"Preview"},{"path":"notes.md"}]}</telegram_attachments>'
        )

        self.assertEqual(visible, "Done.")
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].path, "result.png")
        self.assertEqual(requests[0].kind, "photo")
        self.assertEqual(requests[1].path, "notes.md")

    def test_resolves_only_files_inside_artifacts_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            valid = artifacts / "result.pdf"
            valid.write_bytes(b"%PDF-test")
            outside = root / "outside.pdf"
            outside.write_bytes(b"%PDF-outside")

            self.assertEqual(
                bridge.resolve_artifact_path(artifacts, "result.pdf"),
                valid.resolve(),
            )
            with self.assertRaises(bridge.BridgeError):
                bridge.resolve_artifact_path(artifacts, "../outside.pdf")

    def test_sends_only_configured_number_of_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = make_config(root)
            config.max_artifact_files = 1
            artifacts = root / "artifacts"
            artifacts.mkdir()
            (artifacts / "one.md").write_text("one", encoding="utf-8")
            (artifacts / "two.pdf").write_bytes(b"%PDF-two")
            requests = [
                bridge.ArtifactRequest("one.md", "document", ""),
                bridge.ArtifactRequest("two.pdf", "document", ""),
            ]

            with mock.patch.object(bridge, "send_file", return_value="document") as send_file:
                sent, errors = bridge.send_artifacts(config, "123", artifacts, requests)

            self.assertEqual(sent, ["one.md (document)"])
            self.assertEqual(send_file.call_count, 1)
            self.assertIn("Too many artifacts requested", errors[0])


class UploadTests(unittest.TestCase):
    def test_send_document_uses_multipart_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = make_config(root)
            document = root / "notes.md"
            document.write_text("# Hello", encoding="utf-8")
            captured: dict[str, object] = {}

            class FakeResponse:
                def __enter__(self) -> "FakeResponse":
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

                def read(self) -> bytes:
                    return b'{"ok":true,"result":{"message_id":1}}'

            def fake_urlopen(request: object, timeout: int) -> FakeResponse:
                captured["request"] = request
                captured["timeout"] = timeout
                return FakeResponse()

            with mock.patch.object(bridge.urllib.request, "urlopen", side_effect=fake_urlopen):
                bridge.send_document(config, "123", document, caption="Notes")

            request = captured["request"]
            self.assertTrue(request.full_url.endswith("/sendDocument"))
            self.assertIn(b'name="document"', request.data)
            self.assertIn(b"# Hello", request.data)
            self.assertIn("multipart/form-data", request.headers["Content-type"])

    def test_photo_upload_falls_back_to_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = make_config(root)
            image = root / "result.png"
            image.write_bytes(b"png")

            with mock.patch.object(bridge, "send_photo", side_effect=bridge.BridgeError("bad photo")):
                with mock.patch.object(bridge, "send_document") as send_document:
                    kind = bridge.send_file(config, "123", image, kind="photo")

            self.assertEqual(kind, "document")
            send_document.assert_called_once_with(config, "123", image, "")


class TelegramDownloadRetryTests(unittest.TestCase):
    def test_download_retries_transient_get_file_network_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = make_config(root)
            destination = root / "incoming" / "voice.oga"
            calls = 0

            class FakeResponse:
                def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
                    self.body = body
                    self.headers = headers or {}

                def __enter__(self) -> "FakeResponse":
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

                def read(self, *_args: object) -> bytes:
                    return self.body

            def fake_urlopen(request: object, timeout: int) -> FakeResponse:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise bridge.urllib.error.URLError(
                        ConnectionResetError(54, "Connection reset by peer")
                    )
                if calls == 2:
                    return FakeResponse(b'{"ok":true,"result":{"file_path":"voice/file.oga"}}')
                return FakeResponse(b"audio", {"Content-Length": "5"})

            with mock.patch.object(bridge.time, "sleep") as sleep:
                with mock.patch.object(bridge.urllib.request, "urlopen", side_effect=fake_urlopen):
                    size = bridge.download_telegram_file(config, "voice-id", destination)

            self.assertEqual(size, 5)
            self.assertEqual(destination.read_bytes(), b"audio")
            self.assertEqual(calls, 3)
            sleep.assert_called_once()


class AudioTranscriptionTests(unittest.TestCase):
    def test_transcribe_command_appends_audio_path_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = make_config(root)
            config.audio_transcribe_command = "~/bin/transcribe --lang zh"
            audio = root / "voice.oga"
            audio.write_bytes(b"ogg")

            args = bridge.audio_transcribe_args(config, audio)

            self.assertEqual(args[-2:], ["zh", str(audio)])
            self.assertNotIn("{audio}", " ".join(args))

    def test_transcribes_audio_attachment_from_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = make_config(root)
            config.audio_transcribe_command = "transcribe {audio} --lang zh"
            audio = root / "voice.oga"
            audio.write_bytes(b"ogg")
            attachment = bridge.DownloadedAttachment("audio", audio, audio.name, "audio/ogg", 3)

            def fake_run(args: list[str], **_kwargs: object) -> SimpleNamespace:
                self.assertEqual(args, ["transcribe", str(audio), "--lang", "zh"])
                return SimpleNamespace(returncode=0, stdout="你好\n", stderr="")

            with mock.patch.object(bridge.subprocess, "run", side_effect=fake_run):
                result = bridge.transcribe_audio_attachment(config, attachment)

            self.assertEqual(result.transcript, "你好")


class TtsTests(unittest.TestCase):
    def test_extracts_tts_preference_from_prefixes(self) -> None:
        prompt, preference = bridge.extract_tts_preference("语音回复：讲一下今天安排")
        self.assertEqual(prompt, "讲一下今天安排")
        self.assertIs(preference, True)

        prompt, preference = bridge.extract_tts_preference("只回文字：讲一下今天安排")
        self.assertEqual(prompt, "讲一下今天安排")
        self.assertIs(preference, False)

        prompt, preference = bridge.extract_tts_preference("please reply in voice")
        self.assertEqual(prompt, "please reply in voice")
        self.assertIs(preference, True)

    def test_tts_mode_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(Path(temp_dir))
            config.tts_mode = "on_demand"
            self.assertTrue(bridge.should_send_tts(config, True, inbound_audio=False))
            self.assertFalse(bridge.should_send_tts(config, None, inbound_audio=True))

            config.tts_mode = "mirror"
            self.assertTrue(bridge.should_send_tts(config, None, inbound_audio=True))
            self.assertFalse(bridge.should_send_tts(config, False, inbound_audio=True))

    def test_tts_command_requires_input_and_output_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = make_config(root)
            config.tts_command = "edge-tts --file {input} --write-media {output}"
            input_path = root / "reply.txt"
            output_path = root / "reply.mp3"

            args = bridge.tts_args(config, input_path, output_path, "你好")

            self.assertEqual(args, ["edge-tts", "--file", str(input_path), "--write-media", str(output_path)])

    def test_synthesize_tts_writes_input_and_returns_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = make_config(root)
            config.tts_command = "tts --input {input} --output {output}"

            def fake_run(args: list[str], **_kwargs: object) -> SimpleNamespace:
                output_path = Path(args[-1])
                output_path.write_bytes(b"mp3")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with mock.patch.object(bridge.subprocess, "run", side_effect=fake_run):
                output = bridge.synthesize_tts(config, "你好", root / "tts")

            self.assertEqual(output.name, "reply.mp3")
            self.assertEqual((root / "tts" / "reply.txt").read_text(encoding="utf-8"), "你好")


class CodexInvocationTests(unittest.TestCase):
    def test_resume_invocation_attaches_images_and_describes_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = make_config(root)
            image = root / "image.png"
            image.write_bytes(b"png")
            markdown = root / "notes.md"
            markdown.write_text("# Notes", encoding="utf-8")
            artifacts = root / "artifacts"
            artifacts.mkdir()
            attachments = [
                bridge.DownloadedAttachment("image", image, image.name, "image/png", 3),
                bridge.DownloadedAttachment("markdown", markdown, markdown.name, "text/markdown", 7),
            ]
            captured: dict[str, object] = {}

            def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
                captured["args"] = args
                captured["input"] = kwargs["input"]
                output_path = Path(args[args.index("--output-last-message") + 1])
                output_path.write_text("ok", encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with mock.patch.object(bridge.shutil, "which", return_value="/usr/bin/codex"):
                with mock.patch.object(bridge.subprocess, "run", side_effect=fake_run):
                    reply = bridge.run_codex(
                        config,
                        "Review these files",
                        sender="Tester",
                        attachments=attachments,
                        artifacts_dir=artifacts,
                    )

            self.assertEqual(reply, "ok")
            args = captured["args"]
            assert isinstance(args, list)
            self.assertIn("--image", args)
            self.assertIn(str(image), args)
            prompt = captured["input"]
            assert isinstance(prompt, str)
            self.assertIn(str(markdown), prompt)
            self.assertIn(str(artifacts), prompt)
            self.assertIn("<telegram_attachments>", prompt)

    def test_prompt_includes_audio_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = make_config(root)
            audio = root / "voice.oga"
            audio.write_bytes(b"ogg")
            prompt = bridge.codex_prompt(
                config,
                "Reply to this",
                "Tester",
                attachments=[
                    bridge.DownloadedAttachment(
                        "audio",
                        audio,
                        audio.name,
                        "audio/ogg",
                        3,
                        transcript="明天十点提醒我。",
                    )
                ],
            )

            self.assertIn("Audio transcripts from Telegram", prompt)
            self.assertIn("明天十点提醒我。", prompt)


class HandleUpdateTests(unittest.TestCase):
    def test_image_can_produce_and_send_generated_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = make_config(root)
            update = {
                "message": {
                    "message_id": 21,
                    "chat": {"id": 123},
                    "from": {"id": 123, "first_name": "Tester"},
                    "caption": "Improve this image",
                    "photo": [
                        {
                            "file_id": "photo-id",
                            "file_size": 10,
                            "width": 100,
                            "height": 100,
                        }
                    ],
                }
            }
            captured_artifacts_dir: list[Path] = []

            def fake_download(
                _config: bridge.Config,
                attachment: bridge.IncomingAttachment,
                incoming_dir: Path,
            ) -> bridge.DownloadedAttachment:
                path = incoming_dir / attachment.filename
                path.parent.mkdir(parents=True)
                path.write_bytes(b"image")
                return bridge.DownloadedAttachment(
                    kind="image",
                    path=path,
                    filename=path.name,
                    mime_type="image/jpeg",
                    file_size=5,
                )

            def fake_run(
                _config: bridge.Config,
                _prompt: str,
                sender: str,
                attachments: list[bridge.DownloadedAttachment],
                artifacts_dir: Path,
            ) -> str:
                self.assertEqual(sender, "Tester")
                self.assertEqual(len(attachments), 1)
                output = artifacts_dir / "generated.png"
                output.write_bytes(b"png")
                captured_artifacts_dir.append(artifacts_dir)
                return (
                    "Done.\n"
                    '<telegram_attachments>{"files":[{"path":"generated.png","type":"photo"}]}</telegram_attachments>\n'
                    '<telegram_memory>{"remember":[]}</telegram_memory>'
                )

            with mock.patch.object(bridge, "download_attachment", side_effect=fake_download):
                with mock.patch.object(bridge, "run_codex", side_effect=fake_run):
                    with mock.patch.object(bridge, "send_message") as send_message:
                        with mock.patch.object(bridge, "send_file", return_value="photo") as send_file:
                            bridge.handle_update(config, update)

            send_file.assert_called_once()
            sent_path = send_file.call_args.args[2]
            self.assertEqual(sent_path.name, "generated.png")
            self.assertTrue(any(call.args[2] == "Done." for call in send_message.mock_calls))
            self.assertEqual(len(captured_artifacts_dir), 1)
            self.assertFalse(captured_artifacts_dir[0].exists())

    def test_voice_message_is_transcribed_before_codex(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = make_config(root)
            config.audio_transcribe_command = "transcribe {audio}"
            update = {
                "message": {
                    "message_id": 22,
                    "chat": {"id": 123},
                    "from": {"id": 123, "first_name": "Tester"},
                    "voice": {
                        "file_id": "voice-id",
                        "mime_type": "audio/ogg",
                        "file_size": 10,
                    },
                }
            }

            def fake_download(
                _config: bridge.Config,
                attachment: bridge.IncomingAttachment,
                incoming_dir: Path,
            ) -> bridge.DownloadedAttachment:
                path = incoming_dir / attachment.filename
                path.parent.mkdir(parents=True)
                path.write_bytes(b"voice")
                return bridge.DownloadedAttachment(
                    kind="audio",
                    path=path,
                    filename=path.name,
                    mime_type="audio/ogg",
                    file_size=5,
                )

            def fake_transcribe(
                _config: bridge.Config,
                attachment: bridge.DownloadedAttachment,
            ) -> bridge.DownloadedAttachment:
                return bridge.DownloadedAttachment(
                    attachment.kind,
                    attachment.path,
                    attachment.filename,
                    attachment.mime_type,
                    attachment.file_size,
                    transcript="这是一条语音。",
                )

            def fake_run(
                _config: bridge.Config,
                prompt: str,
                sender: str,
                attachments: list[bridge.DownloadedAttachment],
                artifacts_dir: Path,
            ) -> str:
                self.assertEqual(prompt, "请根据这段语音内容回复。")
                self.assertEqual(sender, "Tester")
                self.assertEqual(attachments[0].transcript, "这是一条语音。")
                self.assertTrue(artifacts_dir.exists())
                return "收到。"

            with mock.patch.object(bridge, "download_attachment", side_effect=fake_download):
                with mock.patch.object(bridge, "transcribe_audio_attachment", side_effect=fake_transcribe):
                    with mock.patch.object(bridge, "run_codex", side_effect=fake_run):
                        with mock.patch.object(bridge, "send_message") as send_message:
                            bridge.handle_update(config, update)

            self.assertTrue(any(call.args[2] == "收到。" for call in send_message.mock_calls))

    def test_voice_message_without_transcriber_gets_config_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = make_config(root)
            update = {
                "message": {
                    "message_id": 23,
                    "chat": {"id": 123},
                    "from": {"id": 123, "first_name": "Tester"},
                    "voice": {
                        "file_id": "voice-id",
                        "mime_type": "audio/ogg",
                        "file_size": 10,
                    },
                }
            }

            with mock.patch.object(bridge, "send_message") as send_message:
                with mock.patch.object(bridge, "download_attachment") as download_attachment:
                    bridge.handle_update(config, update)

            download_attachment.assert_not_called()
            self.assertTrue(
                any("TELEGRAM_AUDIO_TRANSCRIBE_COMMAND" in call.args[2] for call in send_message.mock_calls)
            )

    def test_voice_command_sends_text_and_tts_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = make_config(root)
            config.tts_mode = "on_demand"
            config.tts_command = "tts --input {input} --output {output}"
            update = {
                "message": {
                    "message_id": 24,
                    "chat": {"id": 123},
                    "from": {"id": 123, "first_name": "Tester"},
                    "text": "/voice 讲一下今天安排",
                }
            }

            def fake_synthesize(
                _config: bridge.Config,
                reply: str,
                output_dir: Path,
            ) -> Path:
                self.assertEqual(reply, "今天安排好了。")
                output_dir.mkdir(parents=True, exist_ok=True)
                audio = output_dir / "reply.mp3"
                audio.write_bytes(b"mp3")
                return audio

            with mock.patch.object(bridge, "run_codex", return_value="今天安排好了。"):
                with mock.patch.object(bridge, "send_message") as send_message:
                    with mock.patch.object(bridge, "synthesize_tts", side_effect=fake_synthesize):
                        with mock.patch.object(bridge, "send_audio_reply", return_value="audio") as send_audio:
                            bridge.handle_update(config, update)

            self.assertTrue(any(call.args[2] == "今天安排好了。" for call in send_message.mock_calls))
            send_audio.assert_called_once()

    def test_voice_transcript_can_request_tts_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = make_config(root)
            config.audio_transcribe_command = "transcribe {audio}"
            config.tts_mode = "on_demand"
            config.tts_command = "tts --input {input} --output {output}"
            update = {
                "message": {
                    "message_id": 26,
                    "chat": {"id": 123},
                    "from": {"id": 123, "first_name": "Tester"},
                    "voice": {
                        "file_id": "voice-id",
                        "mime_type": "audio/ogg",
                        "file_size": 10,
                    },
                }
            }

            def fake_download(
                _config: bridge.Config,
                attachment: bridge.IncomingAttachment,
                incoming_dir: Path,
            ) -> bridge.DownloadedAttachment:
                path = incoming_dir / attachment.filename
                path.parent.mkdir(parents=True)
                path.write_bytes(b"voice")
                return bridge.DownloadedAttachment(
                    kind="audio",
                    path=path,
                    filename=path.name,
                    mime_type="audio/ogg",
                    file_size=5,
                )

            def fake_transcribe(
                _config: bridge.Config,
                attachment: bridge.DownloadedAttachment,
            ) -> bridge.DownloadedAttachment:
                return bridge.DownloadedAttachment(
                    attachment.kind,
                    attachment.path,
                    attachment.filename,
                    attachment.mime_type,
                    attachment.file_size,
                    transcript="你用语音回复呀",
                )

            def fake_synthesize(
                _config: bridge.Config,
                reply: str,
                output_dir: Path,
            ) -> Path:
                self.assertEqual(reply, "好。")
                output_dir.mkdir(parents=True, exist_ok=True)
                audio = output_dir / "reply.mp3"
                audio.write_bytes(b"mp3")
                return audio

            with mock.patch.object(bridge, "download_attachment", side_effect=fake_download):
                with mock.patch.object(bridge, "transcribe_audio_attachment", side_effect=fake_transcribe):
                    with mock.patch.object(bridge, "run_codex", return_value="好。"):
                        with mock.patch.object(bridge, "send_message"):
                            with mock.patch.object(bridge, "synthesize_tts", side_effect=fake_synthesize):
                                with mock.patch.object(bridge, "send_audio_reply", return_value="audio") as send_audio:
                                    bridge.handle_update(config, update)

            send_audio.assert_called_once()

    def test_text_command_suppresses_mirror_tts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = make_config(root)
            config.tts_mode = "always"
            config.tts_command = "tts --input {input} --output {output}"
            update = {
                "message": {
                    "message_id": 25,
                    "chat": {"id": 123},
                    "from": {"id": 123, "first_name": "Tester"},
                    "text": "/text 讲一下今天安排",
                }
            }

            with mock.patch.object(bridge, "run_codex", return_value="今天安排好了。"):
                with mock.patch.object(bridge, "send_message"):
                    with mock.patch.object(bridge, "synthesize_tts") as synthesize_tts:
                        bridge.handle_update(config, update)

            synthesize_tts.assert_not_called()


if __name__ == "__main__":
    unittest.main()
