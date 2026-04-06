from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import shutil
import tempfile
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

from accessibility import process_document
from google_workspace import (
    GoogleConfig,
    GoogleWorkspaceError,
    build_authorize_url,
    download_or_export_file,
    exchange_code_for_tokens,
    fetch_user_profile,
    get_drive_file,
    list_drive_files,
    refresh_access_token,
    upload_drive_copy,
)
from storage import SessionStore


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
OUTPUT_DIR = ROOT / "outputs"
UPLOAD_DIR = ROOT / "uploads"
DATA_DIR = ROOT.parent / "data"
SESSION_COOKIE = "ada_session"
STATE_COOKIE = "ada_google_state"
SESSION_TTL_SECONDS = 60 * 60 * 8
session_store = SessionStore(DATA_DIR / "ada_bot.db")


class AdaHandler(BaseHTTPRequestHandler):
    server_version = "AdaComplianceBot/0.2"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path.startswith("/static/"):
            self.serve_file(STATIC_DIR / parsed.path.removeprefix("/static/"))
            return
        if parsed.path == "/download":
            self.handle_download(parsed)
            return
        if parsed.path == "/api/session":
            self.handle_session()
            return
        if parsed.path == "/api/drive/files":
            self.handle_drive_files(parsed)
            return
        if parsed.path == "/healthz":
            self.send_json({"ok": True, "time": int(time.time())})
            return
        if parsed.path == "/auth/google/start":
            self.handle_google_start()
            return
        if parsed.path == "/auth/google/callback":
            self.handle_google_callback(parsed)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/remediate-upload":
            self.handle_local_upload()
            return
        if parsed.path == "/api/remediate-drive":
            self.handle_drive_remediation()
            return
        if parsed.path == "/api/logout":
            self.handle_logout()
            return
        if parsed.path == "/api/review-report":
            self.handle_review_report()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def handle_session(self) -> None:
        session = self.get_session()
        config = google_config()
        payload = {
            "googleConfigured": config.configured,
            "user": session.get("user") if session else None,
            "publicBaseUrl": public_base_url(),
        }
        self.send_json(payload)

    def handle_google_start(self) -> None:
        config = google_config()
        if not config.configured:
            self.redirect("/?authError=google_not_configured")
            return
        state = secrets.token_urlsafe(24)
        auth_url = build_authorize_url(config, state)
        self.send_response(HTTPStatus.FOUND)
        self.set_cookie(STATE_COOKIE, state, max_age=900)
        self.send_header("Location", auth_url)
        self.end_headers()

    def handle_google_callback(self, parsed) -> None:
        query = parse_qs(parsed.query)
        error = query.get("error", [""])[0]
        if error:
            self.redirect(f"/?authError={error}")
            return

        code = query.get("code", [""])[0]
        returned_state = query.get("state", [""])[0]
        state_cookie = self.get_cookie(STATE_COOKIE)
        if not code or not returned_state or returned_state != state_cookie:
            self.redirect("/?authError=state_mismatch")
            return

        try:
            config = google_config()
            tokens = exchange_code_for_tokens(config, code)
            profile = fetch_user_profile(tokens["access_token"])
        except GoogleWorkspaceError as exc:
            self.redirect("/?authError=" + quote_plus(str(exc)))
            return

        session_id = secrets.token_urlsafe(24)
        session_payload = {
            "tokens": tokens,
            "user": {
                "name": profile.get("name", ""),
                "email": profile.get("email", ""),
                "picture": profile.get("picture", ""),
            },
        }
        session_store.save_session(session_id, session_payload, SESSION_TTL_SECONDS)
        self.send_response(HTTPStatus.FOUND)
        self.set_cookie(SESSION_COOKIE, session_id, max_age=SESSION_TTL_SECONDS)
        self.set_cookie(STATE_COOKIE, "", max_age=0)
        self.send_header("Location", "/")
        self.end_headers()

    def handle_drive_files(self, parsed) -> None:
        session = self.require_session()
        if not session:
            return
        query = parse_qs(parsed.query).get("q", [""])[0]
        try:
            files = list_drive_files(self.ensure_access_token(session), query=query)
        except GoogleWorkspaceError as exc:
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
            return
        self.send_json(files)

    def handle_local_upload(self) -> None:
        upload = self.read_multipart_file("document")
        if not upload:
            return
        self.send_json(self.remediate_bytes(upload["filename"], upload["content"]))

    def handle_drive_remediation(self) -> None:
        session = self.require_session()
        if not session:
            return
        payload = self.read_json_body()
        file_id = (payload.get("fileId") or "").strip()
        if not file_id:
            self.send_json({"error": "Missing fileId"}, status=HTTPStatus.BAD_REQUEST)
            return

        temp_dir = Path(tempfile.mkdtemp(prefix="ada_drive_"))
        try:
            access_token = self.ensure_access_token(session)
            source = get_drive_file(access_token, file_id)
            local_path = download_or_export_file(
                access_token,
                file_id,
                source["mimeType"],
                source["name"],
                temp_dir,
            )
            remediation = self.remediate_path(local_path)
            if remediation["supported"] and remediation.get("outputFileName"):
                drive_copy = upload_drive_copy(
                    access_token,
                    OUTPUT_DIR / remediation["outputFileName"],
                    source["name"],
                    source.get("parents", []),
                    source["mimeType"],
                )
                remediation["driveCopy"] = drive_copy
            remediation["source"] = {
                "id": source["id"],
                "name": source["name"],
                "mimeType": source["mimeType"],
                "webViewLink": source.get("webViewLink"),
            }
            self.send_json(remediation)
        except GoogleWorkspaceError as exc:
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def handle_logout(self) -> None:
        session_id = self.get_cookie(SESSION_COOKIE)
        if session_id:
            session_store.delete_session(session_id)
        self.send_response(HTTPStatus.OK)
        self.set_cookie(SESSION_COOKIE, "", max_age=0)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def handle_review_report(self) -> None:
        payload = self.read_json_body()
        document_name = (payload.get("filename") or "untitled-document").strip()
        source_type = (payload.get("documentType") or "unknown").strip()
        report_id = session_store.save_review_report(document_name, source_type, payload)
        self.send_json({"ok": True, "reportId": report_id})

    def handle_download(self, parsed) -> None:
        file_name = parse_qs(parsed.query).get("file", [""])[0]
        target = OUTPUT_DIR / Path(file_name).name
        if target.exists():
            self.serve_file(target, "application/octet-stream", as_attachment=True)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "File not found")

    def remediate_bytes(self, filename: str, content: bytes) -> dict:
        OUTPUT_DIR.mkdir(exist_ok=True)
        UPLOAD_DIR.mkdir(exist_ok=True)
        suffix = Path(filename).suffix
        with tempfile.NamedTemporaryFile(delete=False, dir=UPLOAD_DIR, suffix=suffix) as tmp:
            tmp.write(content)
            upload_path = Path(tmp.name)
        try:
            return self.remediate_path(upload_path, original_name=filename)
        finally:
            upload_path.unlink(missing_ok=True)

    def remediate_path(self, path: Path, original_name: str | None = None) -> dict:
        result = process_document(path, OUTPUT_DIR)
        payload = result.as_dict()
        payload["filename"] = original_name or result.filename
        if result.output_path:
            file_name = Path(result.output_path).name
            payload["outputFileName"] = file_name
            payload["downloadUrl"] = f"/download?file={file_name}"
        return payload

    def ensure_access_token(self, session: dict) -> str:
        tokens = session["tokens"]
        if tokens.get("access_token"):
            return tokens["access_token"]
        if not tokens.get("refresh_token"):
            raise GoogleWorkspaceError("No Google refresh token is available for this session.")
        refreshed = refresh_access_token(google_config(), tokens["refresh_token"])
        refreshed["refresh_token"] = tokens["refresh_token"]
        session["tokens"] = refreshed
        session_id = self.get_cookie(SESSION_COOKIE)
        if session_id:
            session_store.save_session(session_id, session, SESSION_TTL_SECONDS)
        return refreshed["access_token"]

    def read_json_body(self) -> dict:
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def read_multipart_file(self, field_name: str) -> dict | None:
        content_length = int(self.headers.get("content-length", "0"))
        if content_length > max_upload_bytes():
            self.send_json(
                {"error": f"File is too large. Limit uploads to {max_upload_megabytes()} MB."},
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
            return None
        content_type = self.headers.get("content-type", "")
        boundary_match = re.search(r'boundary="?([^";]+)"?', content_type)
        if "multipart/form-data" not in content_type or not boundary_match:
            self.send_json({"error": "Expected multipart/form-data"}, status=HTTPStatus.BAD_REQUEST)
            return None
        body = self.rfile.read(int(self.headers.get("content-length", "0")))
        return parse_multipart_file(body, boundary_match.group(1).encode("utf-8"), field_name)

    def require_session(self) -> dict | None:
        session = self.get_session()
        if session:
            return session
        self.send_json({"error": "Sign in with Google first."}, status=HTTPStatus.UNAUTHORIZED)
        return None

    def get_session(self) -> dict | None:
        session_id = self.get_cookie(SESSION_COOKIE)
        if not session_id:
            return None
        return session_store.get_session(session_id)

    def get_cookie(self, name: str) -> str:
        cookie = SimpleCookie(self.headers.get("Cookie"))
        morsel = cookie.get(name)
        return morsel.value if morsel else ""

    def set_cookie(self, name: str, value: str, max_age: int) -> None:
        cookie = SimpleCookie()
        cookie[name] = value
        cookie[name]["path"] = "/"
        cookie[name]["httponly"] = True
        cookie[name]["max-age"] = str(max_age)
        cookie[name]["samesite"] = "Lax"
        if cookie_should_be_secure():
            cookie[name]["secure"] = True
        self.send_header("Set-Cookie", cookie.output(header="").strip())

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.end_headers()

    def serve_file(self, path: Path, content_type: str | None = None, as_attachment: bool = False) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        guessed_type, _ = mimetypes.guess_type(path.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or guessed_type or "application/octet-stream")
        self.send_header("Content-Length", str(path.stat().st_size))
        if as_attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        with path.open("rb") as handle:
            shutil.copyfileobj(handle, self.wfile)

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def google_config() -> GoogleConfig:
    load_env_file(ROOT.parent / ".env")
    base_url = public_base_url()
    return GoogleConfig(
        client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
        client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        redirect_uri=os.environ.get("GOOGLE_REDIRECT_URI", f"{base_url}/auth/google/callback"),
    )


def main() -> None:
    load_env_file(ROOT.parent / ".env")
    session_store.cleanup_sessions()
    OUTPUT_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    server = ThreadingHTTPServer((host, port), AdaHandler)
    print(f"Serving ADA Compliance Bot on {public_base_url()}")
    server.serve_forever()


def parse_multipart_file(body: bytes, boundary: bytes, field_name: str) -> dict | None:
    marker = b"--" + boundary
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
        if f'name="{field_name}"' not in disposition:
            continue
        filename_match = re.search(r'filename="([^"]+)"', disposition)
        if not filename_match:
            return None
        return {
            "filename": filename_match.group(1),
            "content": content.rstrip(b"\r\n"),
        }
    return None


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def public_base_url() -> str:
    explicit = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    host = os.environ.get("HOST", "127.0.0.1")
    port = os.environ.get("PORT", "8000")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    return f"http://{host}:{port}"


def cookie_should_be_secure() -> bool:
    explicit = os.environ.get("COOKIE_SECURE", "").strip().lower()
    if explicit in {"1", "true", "yes"}:
        return True
    if explicit in {"0", "false", "no"}:
        return False
    return public_base_url().startswith("https://")


def max_upload_megabytes() -> int:
    return int(os.environ.get("MAX_UPLOAD_MB", "20"))


def max_upload_bytes() -> int:
    return max_upload_megabytes() * 1024 * 1024


if __name__ == "__main__":
    main()
