from __future__ import annotations

import base64
import json
import re
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from app.accessibility import process_document


TMP_DIR = Path(tempfile.gettempdir()) / "ada-compliance-bot"
MAX_UPLOAD_MB = 10


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.end_headers()

    def do_POST(self) -> None:
        content_length = int(self.headers.get("content-length", "0"))
        if content_length > MAX_UPLOAD_MB * 1024 * 1024:
            self.send_json(
                {"error": f"File is too large. Limit uploads to {MAX_UPLOAD_MB} MB."},
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
            return

        content_type = self.headers.get("content-type", "")
        boundary_match = re.search(r'boundary="?([^";]+)"?', content_type)
        if "multipart/form-data" not in content_type or not boundary_match:
            self.send_json({"error": "Expected multipart/form-data upload."}, status=HTTPStatus.BAD_REQUEST)
            return

        body = self.rfile.read(content_length)
        form = parse_multipart_form(body, boundary_match.group(1).encode("utf-8"))
        upload = form.get("document")
        if not upload or "filename" not in upload:
            self.send_json({"error": "No document uploaded."}, status=HTTPStatus.BAD_REQUEST)
            return

        review_items = []
        if "reviewState" in form:
            try:
                review_items = json.loads(form["reviewState"].get("value", "[]"))
            except json.JSONDecodeError:
                self.send_json({"error": "Invalid review state payload."}, status=HTTPStatus.BAD_REQUEST)
                return

        TMP_DIR.mkdir(exist_ok=True)
        suffix = Path(upload["filename"]).suffix
        with tempfile.NamedTemporaryFile(delete=False, dir=TMP_DIR, suffix=suffix) as tmp:
            tmp.write(upload["content"])
            upload_path = Path(tmp.name)

        try:
            result = process_document(upload_path, TMP_DIR, review_items=review_items)
            payload = result.as_dict()
            payload["filename"] = upload["filename"]
            if result.output_path:
                output_path = Path(result.output_path)
                payload["outputFileName"] = output_path.name
                payload["outputMimeType"] = guess_mime(output_path)
                payload["outputFileBase64"] = base64.b64encode(output_path.read_bytes()).decode("ascii")
            self.send_json(payload)
        finally:
            upload_path.unlink(missing_ok=True)
            if "result" in locals() and result.output_path:
                Path(result.output_path).unlink(missing_ok=True)

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(encoded)


def parse_multipart_form(body: bytes, boundary: bytes) -> dict[str, dict]:
    marker = b"--" + boundary
    fields: dict[str, dict] = {}
    for part in body.split(marker):
        part = part.strip()
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        header_blob, content = part.split(b"\r\n\r\n", 1)
        headers = header_blob.decode("utf-8", errors="ignore")
        disposition = next(
            (line for line in headers.split("\r\n") if line.lower().startswith("content-disposition:")),
            "",
        )
        name_match = re.search(r'name="([^"]+)"', disposition)
        if not name_match:
            continue
        field_name = name_match.group(1)
        filename_match = re.search(r'filename="([^"]+)"', disposition)
        if filename_match:
            fields[field_name] = {
                "filename": filename_match.group(1),
                "content": content.rstrip(b"\r\n"),
            }
        else:
            fields[field_name] = {
                "value": content.rstrip(b"\r\n").decode("utf-8", errors="ignore"),
            }
    return fields


def guess_mime(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if ext == ".pptx":
        return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    return "application/octet-stream"
