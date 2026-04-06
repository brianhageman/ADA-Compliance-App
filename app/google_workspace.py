from __future__ import annotations

import json
import mimetypes
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"

SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.readonly",
]

GOOGLE_EXPORTS = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
}

OFFICE_MIME_BY_EXT = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf",
}


class GoogleWorkspaceError(Exception):
    pass


@dataclass
class GoogleConfig:
    client_id: str
    client_secret: str
    redirect_uri: str

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)


def build_authorize_url(config: GoogleConfig, state: str) -> str:
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code_for_tokens(config: GoogleConfig, code: str) -> dict[str, Any]:
    payload = urlencode(
        {
            "code": code,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "redirect_uri": config.redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    return google_json_request(TOKEN_URL, data=payload)


def refresh_access_token(config: GoogleConfig, refresh_token: str) -> dict[str, Any]:
    payload = urlencode(
        {
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    return google_json_request(TOKEN_URL, data=payload)


def fetch_user_profile(access_token: str) -> dict[str, Any]:
    return google_json_request(USERINFO_URL, access_token=access_token)


def list_drive_files(access_token: str, query: str = "") -> dict[str, Any]:
    q = query.strip()
    drive_query = [
        "trashed = false",
        "("
        "mimeType = 'application/vnd.google-apps.document' or "
        "mimeType = 'application/vnd.google-apps.presentation' or "
        "mimeType = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' or "
        "mimeType = 'application/vnd.openxmlformats-officedocument.presentationml.presentation' or "
        "mimeType = 'application/pdf'"
        ")",
    ]
    if q:
        escaped = q.replace("'", "\\'")
        drive_query.append(f"name contains '{escaped}'")

    params = urlencode(
        {
            "pageSize": "25",
            "fields": "files(id,name,mimeType,webViewLink,modifiedTime,owners(displayName,emailAddress))",
            "orderBy": "modifiedTime desc",
            "q": " and ".join(drive_query),
        }
    )
    return google_json_request(f"{DRIVE_FILES_URL}?{params}", access_token=access_token)


def get_drive_file(access_token: str, file_id: str) -> dict[str, Any]:
    params = urlencode(
        {
            "fields": "id,name,mimeType,parents,webViewLink",
        }
    )
    return google_json_request(f"{DRIVE_FILES_URL}/{file_id}?{params}", access_token=access_token)


def download_or_export_file(access_token: str, file_id: str, mime_type: str, file_name: str, dest_dir: Path) -> Path:
    if mime_type in GOOGLE_EXPORTS:
        export_mime, suffix = GOOGLE_EXPORTS[mime_type]
        url = f"{DRIVE_FILES_URL}/{file_id}/export?{urlencode({'mimeType': export_mime})}"
        local_path = dest_dir / f"{Path(file_name).stem}{suffix}"
        payload = google_binary_request(url, access_token=access_token)
        local_path.write_bytes(payload)
        return local_path

    suffix = Path(file_name).suffix or guess_suffix(mime_type)
    local_path = dest_dir / f"{Path(file_name).stem}{suffix}"
    payload = google_binary_request(f"{DRIVE_FILES_URL}/{file_id}?alt=media", access_token=access_token)
    local_path.write_bytes(payload)
    return local_path


def upload_drive_copy(
    access_token: str,
    src_path: Path,
    original_name: str,
    parent_ids: list[str] | None,
    source_mime_type: str,
) -> dict[str, Any]:
    ext = src_path.suffix.lower()
    upload_mime = OFFICE_MIME_BY_EXT.get(ext) or mimetypes.guess_type(src_path.name)[0] or "application/octet-stream"
    metadata = {
        "name": build_accessible_name(original_name, ext),
    }
    if parent_ids:
        metadata["parents"] = parent_ids

    boundary = f"ada-bot-{secrets.token_hex(8)}"
    body = build_multipart_related(
        boundary=boundary,
        metadata=metadata,
        media_bytes=src_path.read_bytes(),
        media_mime=upload_mime,
    )
    url = f"{DRIVE_UPLOAD_URL}?uploadType=multipart&fields=id,name,mimeType,webViewLink"
    headers = {
        "Content-Type": f'multipart/related; boundary="{boundary}"',
    }
    return google_json_request(url, data=body, access_token=access_token, headers=headers)


def build_accessible_name(original_name: str, ext: str) -> str:
    stem = Path(original_name).stem
    return f"{stem} - ADA accessible copy{ext}"


def build_multipart_related(boundary: str, metadata: dict[str, Any], media_bytes: bytes, media_mime: str) -> bytes:
    segments = [
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(metadata)}\r\n".encode("utf-8"),
        f"--{boundary}\r\nContent-Type: {media_mime}\r\n\r\n".encode("utf-8"),
        media_bytes + b"\r\n",
        f"--{boundary}--\r\n".encode("utf-8"),
    ]
    return b"".join(segments)


def google_json_request(
    url: str,
    *,
    data: bytes | None = None,
    access_token: str | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request_headers = {
        "Accept": "application/json",
    }
    if data is not None and "Content-Type" not in (headers or {}):
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    if access_token:
        request_headers["Authorization"] = f"Bearer {access_token}"
    if headers:
        request_headers.update(headers)

    request = Request(url, data=data, headers=request_headers)
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise GoogleWorkspaceError(detail or f"Google API error {exc.code}") from exc
    except URLError as exc:
        raise GoogleWorkspaceError(f"Network error while contacting Google: {exc.reason}") from exc


def google_binary_request(url: str, *, access_token: str) -> bytes:
    request = Request(url, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urlopen(request, timeout=30) as response:
            return response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise GoogleWorkspaceError(detail or f"Google API error {exc.code}") from exc
    except URLError as exc:
        raise GoogleWorkspaceError(f"Network error while contacting Google: {exc.reason}") from exc


def guess_suffix(mime_type: str) -> str:
    if mime_type in GOOGLE_EXPORTS:
        return GOOGLE_EXPORTS[mime_type][1]
    if mime_type == "application/pdf":
        return ".pdf"
    return ""
