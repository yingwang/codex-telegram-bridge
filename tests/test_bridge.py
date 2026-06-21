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


if __name__ == "__main__":
    unittest.main()
