from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
import xml.etree.ElementTree as ET


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
}

GENERIC_LINK_TEXT = {
    "click here",
    "here",
    "link",
    "this link",
    "more",
    "read more",
}


@dataclass
class Issue:
    severity: str
    category: str
    message: str
    location: str


@dataclass
class ReviewItem:
    review_id: str
    category: str
    title: str
    prompt: str
    location: str
    suggested_value: str = ""
    confidence: str = "medium"
    priority: str = "medium"
    status: str = "needs_review"


@dataclass
class AuditSummary:
    score: int
    auto_applied: int
    needs_review: int
    manual_checks: int


@dataclass
class ProcessResult:
    supported: bool
    document_type: str
    filename: str
    issues: list[Issue] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    review_items: list[ReviewItem] = field(default_factory=list)
    output_path: str | None = None
    limitations: list[str] = field(default_factory=list)
    audit_summary: AuditSummary | None = None

    def as_dict(self) -> dict:
        summary = self.audit_summary or build_audit_summary(self)
        return {
            "supported": self.supported,
            "documentType": self.document_type,
            "filename": self.filename,
            "issues": [issue.__dict__ for issue in self.issues],
            "changes": self.changes,
            "reviewItems": [item.__dict__ for item in self.review_items],
            "outputPath": self.output_path,
            "limitations": self.limitations,
            "auditSummary": summary.__dict__,
        }


def process_document(upload_path: Path, output_dir: Path) -> ProcessResult:
    ext = upload_path.suffix.lower()
    if ext == ".docx":
        return remediate_docx(upload_path, output_dir)
    if ext == ".pptx":
        return remediate_pptx(upload_path, output_dir)
    if ext in {".pdf", ".doc", ".ppt", ".xls", ".xlsx", ".txt"}:
        return unsupported_result(upload_path, ext)
    return unsupported_result(upload_path, ext)


def unsupported_result(upload_path: Path, ext: str) -> ProcessResult:
    limitations = [
        "This web app can auto-remediate PowerPoint (.pptx) and Word (.docx) files after direct upload.",
        "PDF remediation usually requires structure tagging and reading-order tools that are not implemented yet.",
        "Google login and Drive integration have been removed from this version so teachers can use a simple upload-and-download workflow.",
    ]
    issues = [
        Issue(
            severity="info",
            category="support",
            message=f"{ext or 'This file type'} is not editable in the current offline MVP.",
            location="document",
        )
    ]
    result = ProcessResult(
        supported=False,
        document_type=ext.lstrip(".") or "unknown",
        filename=upload_path.name,
        issues=issues,
        limitations=limitations,
    )
    result.review_items.append(
        ReviewItem(
            review_id="manual-support-review",
            category="workflow",
            title="Choose a manual review workflow",
            prompt="This file type still needs a specialist accessibility review before distribution.",
            location="document",
            suggested_value="Open with your document accessibility checker and review tags, headings, reading order, and contrast manually.",
            confidence="high",
            priority="high",
        )
    )
    result.audit_summary = build_audit_summary(result)
    return result


def remediate_docx(upload_path: Path, output_dir: Path) -> ProcessResult:
    result = ProcessResult(
        supported=True,
        document_type="docx",
        filename=upload_path.name,
        limitations=[
            "Alt text is generated from image filenames, which should be reviewed by a human.",
            "Color contrast and heading structure are not fully remediated in this MVP.",
        ],
    )
    work_dir = Path(tempfile.mkdtemp(prefix="docx_fix_"))
    try:
        unzip_to(upload_path, work_dir)
        rel_targets = load_relationship_targets(work_dir / "word" / "_rels" / "document.xml.rels")
        document_path = work_dir / "word" / "document.xml"
        tree = ET.parse(document_path)
        root = tree.getroot()

        for index, doc_pr in enumerate(root.findall(".//wp:docPr", NS), start=1):
            current_descr = (doc_pr.attrib.get("descr") or "").strip()
            if current_descr:
                continue
            generated = build_alt_text(doc_pr.attrib.get("name"), index)
            doc_pr.set("descr", generated)
            doc_pr.set("title", generated)
            result.issues.append(
                Issue(
                    severity="warning",
                    category="alt_text",
                    message=f"Missing alt text replaced with '{generated}'.",
                    location=f"image {index}",
                )
            )
            result.changes.append(f"Added alt text for image {index}.")
            result.review_items.append(
                ReviewItem(
                    review_id=f"docx-alt-{index}",
                    category="alt_text",
                    title="Review generated alt text",
                    prompt="Confirm whether this image needs meaningful alt text or should be marked decorative.",
                    location=f"image {index}",
                    suggested_value=generated,
                    confidence="low",
                    priority="high",
                )
            )

        hyperlinks = root.findall(".//w:hyperlink", NS)
        for index, hyperlink in enumerate(hyperlinks, start=1):
            text_nodes = hyperlink.findall(".//w:t", NS)
            visible_text = "".join(node.text or "" for node in text_nodes).strip()
            if not visible_text or normalize_link_text(visible_text) not in GENERIC_LINK_TEXT:
                continue
            rel_id = hyperlink.attrib.get(f"{{{NS['r']}}}id")
            target = rel_targets.get(rel_id, "")
            new_text = describe_target(target)
            if new_text == visible_text:
                continue
            replaced = False
            for node in text_nodes:
                if not replaced:
                    node.text = new_text
                    replaced = True
                else:
                    node.text = ""
            result.issues.append(
                Issue(
                    severity="warning",
                    category="link_text",
                    message=f"Generic link text '{visible_text}' replaced with '{new_text}'.",
                    location=f"hyperlink {index}",
                )
            )
            result.changes.append(f"Rewrote hyperlink {index} text.")
            result.review_items.append(
                ReviewItem(
                    review_id=f"docx-link-{index}",
                    category="link_text",
                    title="Confirm rewritten link text",
                    prompt="Check that the new hyperlink label accurately matches the instructional purpose of the link.",
                    location=f"hyperlink {index}",
                    suggested_value=new_text,
                    confidence="medium",
                    priority="medium",
                )
            )

        audit_docx_structure(root, result)

        tree.write(document_path, encoding="utf-8", xml_declaration=True)
        output_path = output_dir / f"{upload_path.stem}.ada-remediated.docx"
        zip_from(work_dir, output_path)
        result.output_path = str(output_path)
        result.audit_summary = build_audit_summary(result)
        return result
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def remediate_pptx(upload_path: Path, output_dir: Path) -> ProcessResult:
    result = ProcessResult(
        supported=True,
        document_type="pptx",
        filename=upload_path.name,
        limitations=[
            "This MVP remediates image alt text only for PowerPoint files.",
            "Color contrast, reading order, and slide title enforcement are planned next steps.",
        ],
    )
    work_dir = Path(tempfile.mkdtemp(prefix="pptx_fix_"))
    try:
        unzip_to(upload_path, work_dir)
        slide_paths = sorted((work_dir / "ppt" / "slides").glob("slide*.xml"))
        total_updates = 0

        for slide_idx, slide_path in enumerate(slide_paths, start=1):
            tree = ET.parse(slide_path)
            root = tree.getroot()
            changed = False
            for image_idx, props in enumerate(root.findall(".//p:cNvPr", NS), start=1):
                name = (props.attrib.get("name") or "").lower()
                descr = (props.attrib.get("descr") or "").strip()
                if descr or "picture" not in name and "image" not in name and "pic" not in name:
                    continue
                generated = build_alt_text(props.attrib.get("name"), image_idx)
                props.set("descr", generated)
                props.set("title", generated)
                changed = True
                total_updates += 1
                result.issues.append(
                    Issue(
                        severity="warning",
                        category="alt_text",
                        message=f"Missing alt text replaced with '{generated}'.",
                        location=f"slide {slide_idx}, image {image_idx}",
                    )
                )
                result.changes.append(f"Added alt text on slide {slide_idx} image {image_idx}.")
                result.review_items.append(
                    ReviewItem(
                        review_id=f"pptx-alt-{slide_idx}-{image_idx}",
                        category="alt_text",
                        title="Review slide image alt text",
                        prompt="Confirm the generated alt text or replace it with a content-specific description.",
                        location=f"slide {slide_idx}, image {image_idx}",
                        suggested_value=generated,
                        confidence="low",
                        priority="high",
                    )
                )
            audit_slide_structure(root, slide_idx, result)
            if changed:
                tree.write(slide_path, encoding="utf-8", xml_declaration=True)

        output_path = output_dir / f"{upload_path.stem}.ada-remediated.pptx"
        zip_from(work_dir, output_path)
        result.output_path = str(output_path)
        if total_updates == 0:
            result.changes.append("No missing PowerPoint alt text was found.")
        result.audit_summary = build_audit_summary(result)
        return result
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def unzip_to(src: Path, dest: Path) -> None:
    with zipfile.ZipFile(src, "r") as archive:
        archive.extractall(dest)


def zip_from(src_dir: Path, output_path: Path) -> None:
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in src_dir.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(src_dir))


def load_relationship_targets(rels_path: Path) -> dict[str, str]:
    if not rels_path.exists():
        return {}
    tree = ET.parse(rels_path)
    root = tree.getroot()
    return {
        rel.attrib.get("Id", ""): rel.attrib.get("Target", "")
        for rel in root.findall("{http://schemas.openxmlformats.org/package/2006/relationships}Relationship")
    }


def build_alt_text(name: str | None, index: int) -> str:
    raw_name = (name or f"image {index}").strip()
    words = re.sub(r"[_-]+", " ", raw_name)
    words = re.sub(r"\s+", " ", words)
    return f"Describe this image: {words}".strip()


def normalize_link_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def describe_target(target: str) -> str:
    if not target:
        return "Open referenced resource"
    parsed = urlparse(target)
    if parsed.scheme and parsed.netloc:
        host = parsed.netloc.replace("www.", "")
        path = parsed.path.strip("/")
        if path:
            return f"Visit {host}/{path}"
        return f"Visit {host}"
    return f"Open {target.strip('/')}"


def result_json(result: ProcessResult) -> str:
    return json.dumps(result.as_dict())


def audit_docx_structure(root: ET.Element, result: ProcessResult) -> None:
    paragraphs = root.findall(".//w:p", NS)
    heading_candidates = 0
    raw_url_count = 0
    for index, paragraph in enumerate(paragraphs, start=1):
        texts = [node.text or "" for node in paragraph.findall(".//w:t", NS)]
        visible_text = "".join(texts).strip()
        if not visible_text:
            continue

        style = paragraph.find(".//w:pStyle", NS)
        style_val = style.attrib.get(f"{{{NS['w']}}}val", "") if style is not None else ""
        if looks_like_heading_candidate(visible_text, style_val):
            heading_candidates += 1
            if heading_candidates <= 3:
                result.review_items.append(
                    ReviewItem(
                        review_id=f"docx-heading-{index}",
                        category="heading_structure",
                        title="Check heading structure",
                        prompt="This paragraph looks like a heading visually. Verify that a true heading style is applied.",
                        location=f"paragraph {index}",
                        suggested_value=visible_text,
                        confidence="medium",
                        priority="medium",
                    )
                )

        if re.search(r"https?://\S+", visible_text) and not paragraph.findall(".//w:hyperlink", NS):
            raw_url_count += 1
            result.review_items.append(
                ReviewItem(
                    review_id=f"docx-raw-url-{index}",
                    category="link_text",
                    title="Replace raw URL with descriptive link text",
                    prompt="This paragraph contains a pasted URL. Replace it with text that tells students where the link goes or what it does.",
                    location=f"paragraph {index}",
                    suggested_value=visible_text,
                    confidence="high",
                    priority="medium",
                )
            )

    if heading_candidates:
        result.issues.append(
            Issue(
                severity="info",
                category="heading_structure",
                message=f"Found {heading_candidates} possible heading-style paragraphs to review.",
                location="document",
            )
        )
    if raw_url_count:
        result.issues.append(
            Issue(
                severity="info",
                category="link_text",
                message=f"Found {raw_url_count} pasted URL paragraphs that may need descriptive link text.",
                location="document",
            )
        )


def audit_slide_structure(root: ET.Element, slide_idx: int, result: ProcessResult) -> None:
    texts = [
        (node.text or "").strip()
        for node in root.findall(".//a:t", NS)
        if (node.text or "").strip()
    ]
    if not texts:
        result.review_items.append(
            ReviewItem(
                review_id=f"pptx-slide-title-{slide_idx}",
                category="slide_title",
                title="Check slide title and reading order",
                prompt="This slide has no visible text. Confirm it has a meaningful title and that screen reader order still makes sense.",
                location=f"slide {slide_idx}",
                suggested_value="Add a concise slide title.",
                confidence="medium",
                priority="high",
            )
        )
        result.issues.append(
            Issue(
                severity="info",
                category="slide_title",
                message="A slide appears to have no visible text and may need a title or reading-order review.",
                location=f"slide {slide_idx}",
            )
        )


def looks_like_heading_candidate(text: str, style_val: str) -> bool:
    if style_val.lower().startswith("heading"):
        return False
    compact = text.strip()
    if len(compact) > 80:
        return False
    if compact.endswith((".", "!", "?")):
        return False
    if compact.isupper() and len(compact.split()) <= 8:
        return True
    title_case_ratio = sum(1 for word in compact.split() if word[:1].isupper()) / max(len(compact.split()), 1)
    return len(compact.split()) <= 10 and title_case_ratio > 0.7


def build_audit_summary(result: ProcessResult) -> AuditSummary:
    issue_penalty = min(len(result.issues) * 4, 28)
    review_penalty = min(len(result.review_items) * 6, 42)
    score = max(0, 100 - issue_penalty - review_penalty)
    manual_checks = len([item for item in result.review_items if item.status == "needs_review"])
    return AuditSummary(
        score=score,
        auto_applied=len(result.changes),
        needs_review=len(result.review_items),
        manual_checks=manual_checks,
    )
