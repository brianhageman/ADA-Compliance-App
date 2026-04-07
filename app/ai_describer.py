from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-4.1-mini"


def describe_image_file(image_path: Path, *, fallback_name: str, index: int, context_hint: str = "") -> str:
    fallback = fallback_alt_text(fallback_name, index, context_hint=context_hint)
    if not image_path.exists() or not openai_configured():
        return fallback

    mime_type = mime_for_path(image_path)
    if not mime_type:
        return fallback

    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    prompt = (
        "Write concise accessibility alt text for a classroom document image. "
        "State what is visually present, not just the topic. "
        "If it is a chart, include the chart type and the main trend or comparison. "
        "If it is a diagram, mention the main parts, labels, arrows, or flow. "
        "If it is a worksheet image or illustration, name the visible subject and what students are meant to notice. "
        "Do not use vague phrases like image, picture, graphic, educational illustration, or visual unless they are necessary for clarity. "
        "Return plain text only, ideally one sentence and under 160 characters."
    )
    if context_hint.strip():
        prompt += f" Nearby document context: {context_hint.strip()}"
    try:
        response = call_openai(
            {
                "model": os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {
                                "type": "input_image",
                                "image_url": f"data:{mime_type};base64,{image_b64}",
                                "detail": "high",
                            },
                        ],
                    }
                ],
            }
        )
        text = extract_output_text(response)
        return finalize_alt_text(text, fallback, context_hint=context_hint)
    except Exception:
        return fallback


def describe_table_rows(rows: list[list[str]], *, fallback_title: str) -> str:
    if not rows:
        return fallback_title

    if not openai_configured():
        return fallback_table_summary(rows, fallback_title)

    serialized_rows = []
    for row in rows[:6]:
        serialized_rows.append(" | ".join(cell.strip() for cell in row[:6] if cell.strip()))
    prompt = (
        "Write a concise accessibility description for a classroom document table. "
        "Mention the table's apparent subject, structure, and the kind of information it contains. "
        "Return plain text only, ideally one or two short sentences.\n\n"
        + "\n".join(serialized_rows)
    )
    try:
        response = call_openai(
            {
                "model": os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
                "input": prompt,
            }
        )
        text = extract_output_text(response)
        return text.strip() or fallback_table_summary(rows, fallback_title)
    except Exception:
        return fallback_table_summary(rows, fallback_title)


def openai_configured() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def call_openai(payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        RESPONSES_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=40) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(exc.read().decode("utf-8", errors="ignore") or str(exc)) from exc
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def finalize_alt_text(text: str, fallback: str, *, context_hint: str = "") -> str:
    candidate = normalize_alt_text_response(text)
    if not candidate:
        return fallback
    if looks_like_generic_alt_text(candidate):
        context_label = cleaned_context_hint(context_hint)
        if context_label:
            return fallback_alt_text("", 0, context_hint=context_label)
        return fallback
    return candidate


def normalize_alt_text_response(text: str) -> str:
    candidate = re.sub(r"\s+", " ", (text or "").strip())
    candidate = candidate.strip("\"'")
    if len(candidate) > 180:
        candidate = candidate[:177].rstrip(" ,;:-") + "..."
    return candidate


def looks_like_generic_alt_text(text: str) -> bool:
    normalized = text.lower()
    generic_starts = (
        "image of ",
        "picture of ",
        "graphic of ",
        "illustration of ",
        "educational illustration",
        "visual showing",
        "illustration related to",
    )
    generic_phrases = (
        "main subject and purpose",
        "used in the document",
        "depicts a concept",
        "related to the topic",
    )
    if any(normalized.startswith(prefix) for prefix in generic_starts) and len(normalized.split()) <= 8:
        return True
    if any(phrase in normalized for phrase in generic_phrases):
        return True
    if len(normalized.split()) <= 4:
        return True
    return False


def extract_output_text(response: dict) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    for item in response.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                return text
    return ""


def mime_for_path(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext == ".webp":
        return "image/webp"
    if ext == ".gif":
        return "image/gif"
    return None


def fallback_alt_text(name: str, index: int, context_hint: str = "") -> str:
    cleaned = cleaned_asset_name(name, index)
    context_label = cleaned_context_hint(context_hint)
    subject = normalize_subject_phrase(context_label or cleaned)
    if subject:
        lowered = subject.lower()
        if any(keyword in lowered for keyword in ("chart", "graph", "plot")):
            return f"Chart showing {subject}."
        if any(keyword in lowered for keyword in ("diagram", "cycle", "process", "model")):
            return f"Diagram showing {subject}."
        if any(keyword in lowered for keyword in ("map", "timeline", "sequence")):
            return f"Visual showing {subject}."
        return f"Illustration related to {subject}."
    return "Educational illustration used in the document."


def fallback_table_summary(rows: list[list[str]], fallback_title: str) -> str:
    row_count = len(rows)
    col_count = max((len(row) for row in rows), default=0)
    header = ", ".join(cell for cell in rows[0][:4] if cell.strip()) if rows else ""
    if header:
        return f"Table with {row_count} rows and {col_count} columns. Header examples: {header}."
    return f"{fallback_title}. Table with {row_count} rows and {col_count} columns."


def normalize_subject_phrase(value: str) -> str:
    subject = (value or "").strip()
    subject = re.sub(r"^(this|the|an?)\s+", "", subject, flags=re.IGNORECASE)
    return subject.strip(" .,:;-")


def cleaned_context_hint(value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    if not text:
        return ""
    sentences = [segment.strip(" .,:;-") for segment in re.split(r"[.!?]", text) if segment.strip()]
    if not sentences:
        return ""
    preferred = next(
        (
            sentence
            for sentence in sentences
            if any(keyword in sentence.lower() for keyword in ("chart", "graph", "diagram", "cycle", "timeline", "map", "process", "model"))
        ),
        sentences[0],
    )
    words = preferred.split()
    limited = " ".join(words[:12]).strip(" .,:;-")
    if len(limited) < 4:
        return ""
    return limited


def cleaned_asset_name(name: str, index: int) -> str:
    raw_name = (name or f"image {index}").strip()
    stem = Path(raw_name).stem
    normalized = re.sub(r"[_-]+", " ", stem)
    normalized = re.sub(r"\s+", " ", normalized).strip(" ._")
    if not normalized:
        return ""
    if is_generic_asset_name(normalized):
        return ""
    return normalized


def is_generic_asset_name(value: str) -> bool:
    compact = re.sub(r"[^a-z0-9]+", "", value.lower())
    generic_patterns = (
        r"image\d*",
        r"img\d*",
        r"picture\d*",
        r"photo\d*",
        r"graphic\d*",
        r"diagram\d*",
        r"chart\d*",
        r"figure\d*",
        r"scan\d*",
        r"screenshot\d*",
    )
    return any(re.fullmatch(pattern, compact) for pattern in generic_patterns)
