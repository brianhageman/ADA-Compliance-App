from __future__ import annotations

import copy
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

from app.ai_describer import describe_image_file, describe_table_rows, fallback_alt_text as generated_fallback_alt_text


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
    preview_text: str = ""
    secondary_text: str = ""


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


def process_document(upload_path: Path, output_dir: Path, review_items: list[dict] | None = None) -> ProcessResult:
    ext = upload_path.suffix.lower()
    if ext == ".docx":
        return remediate_docx(upload_path, output_dir, review_items=review_items)
    if ext == ".pptx":
        return remediate_pptx(upload_path, output_dir, review_items=review_items)
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
            message=f"{ext or 'This file type'} is not editable in the current web app.",
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


def remediate_docx(upload_path: Path, output_dir: Path, review_items: list[dict] | None = None) -> ProcessResult:
    result = ProcessResult(
        supported=True,
        document_type="docx",
        filename=upload_path.name,
        limitations=[
            "AI-assisted image and table descriptions should still be reviewed by a human before sharing with students.",
            "Color contrast and heading structure are not fully remediated in this MVP.",
        ],
    )
    work_dir = Path(tempfile.mkdtemp(prefix="docx_fix_"))
    try:
        unzip_to(upload_path, work_dir)
        rel_targets = load_relationship_targets(work_dir / "word" / "_rels" / "document.xml.rels")
        media_files = sorted((work_dir / "word" / "media").glob("*"))
        document_path = work_dir / "word" / "document.xml"
        tree = ET.parse(document_path)
        root = tree.getroot()

        for index, doc_pr in enumerate(root.findall(".//wp:docPr", NS), start=1):
            current_descr = (doc_pr.attrib.get("descr") or "").strip()
            if current_descr and not should_replace_existing_alt_text(current_descr):
                continue
            fallback_name = doc_pr.attrib.get("name") or f"image {index}"
            if index - 1 < len(media_files):
                generated = describe_image_file(
                    media_files[index - 1],
                    fallback_name=fallback_name,
                    index=index,
                    context_hint=docx_image_context(root, doc_pr),
                )
            else:
                generated = build_alt_text(fallback_name, index)
            doc_pr.set("descr", generated)
            doc_pr.set("title", generated)
            alt_issue = "Missing alt text replaced" if not current_descr else "Generic alt text replaced"
            result.issues.append(
                Issue(
                    severity="warning",
                    category="alt_text",
                    message=f"{alt_issue} with '{generated}'.",
                    location=f"image {index}",
                )
            )
            result.changes.append(f"Updated alt text for image {index}.")
            result.review_items.append(
                ReviewItem(
                    review_id=f"docx-alt-{index}",
                    category="alt_text",
                    title="Review generated alt text",
                    prompt="Confirm whether this image needs meaningful alt text or should be marked decorative.",
                    location=f"image {index}",
                    suggested_value=generated,
                    confidence="medium",
                    priority="high",
                    preview_text=image_review_preview(doc_pr.attrib.get("name"), current_descr, docx_image_context(root, doc_pr)),
                    secondary_text=f"Suggested alt text: {generated}",
                )
            )

        tables = root.findall(".//w:tbl", NS)
        for index, table in enumerate(tables, start=1):
            if get_table_description(table):
                continue
            rows = extract_table_rows(table)
            if not rows:
                continue
            generated = describe_table_rows(rows, fallback_title=f"Table {index}")
            set_table_description(table, generated)
            result.issues.append(
                Issue(
                    severity="warning",
                    category="table_text",
                    message=f"Added table description for table {index}.",
                    location=f"table {index}",
                )
            )
            result.changes.append(f"Added accessibility description for table {index}.")
            result.review_items.append(
                ReviewItem(
                    review_id=f"docx-table-{index}",
                    category="table_text",
                    title="Review generated table description",
                    prompt="Confirm that this table description explains the table's subject and structure clearly for a screen reader user.",
                    location=f"table {index}",
                    suggested_value=generated,
                    confidence="medium",
                    priority="high",
                    preview_text=table_preview_text(rows),
                    secondary_text=f"Suggested description: {generated}",
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
            replace_text_nodes(text_nodes, new_text)
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
                    preview_text=visible_text,
                    secondary_text=f"Suggested link text: {new_text}",
                )
            )

        audit_docx_structure(root, result)
        apply_docx_review_actions(root, result, review_items)

        tree.write(document_path, encoding="utf-8", xml_declaration=True)
        output_path = output_dir / f"{upload_path.stem}.ada-remediated.docx"
        zip_from(work_dir, output_path)
        result.output_path = str(output_path)
        result.audit_summary = build_audit_summary(result)
        return result
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def remediate_pptx(upload_path: Path, output_dir: Path, review_items: list[dict] | None = None) -> ProcessResult:
    result = ProcessResult(
        supported=True,
        document_type="pptx",
        filename=upload_path.name,
        limitations=[
            "AI-assisted image descriptions should still be reviewed by a human before distribution.",
            "Color contrast, reading order, and slide title enforcement are planned next steps.",
        ],
    )
    work_dir = Path(tempfile.mkdtemp(prefix="pptx_fix_"))
    try:
        unzip_to(upload_path, work_dir)
        slide_paths = sorted((work_dir / "ppt" / "slides").glob("slide*.xml"))
        media_files = sorted((work_dir / "ppt" / "media").glob("*"))
        total_updates = 0
        slide_docs: dict[int, tuple[Path, ET.ElementTree, ET.Element]] = {}
        media_index = 0

        for slide_idx, slide_path in enumerate(slide_paths, start=1):
            tree = ET.parse(slide_path)
            root = tree.getroot()
            slide_docs[slide_idx] = (slide_path, tree, root)
            for image_idx, props in enumerate(root.findall(".//p:cNvPr", NS), start=1):
                name = (props.attrib.get("name") or "").lower()
                descr = (props.attrib.get("descr") or "").strip()
                if descr and not should_replace_existing_alt_text(descr):
                    continue
                if "picture" not in name and "image" not in name and "pic" not in name:
                    continue
                fallback_name = props.attrib.get("name") or f"slide {slide_idx} image {image_idx}"
                media_file = media_files[media_index] if media_index < len(media_files) else None
                media_index += 1
                if media_file is not None:
                    generated = describe_image_file(
                        media_file,
                        fallback_name=fallback_name,
                        index=image_idx,
                        context_hint=slide_context_hint(root),
                    )
                else:
                    generated = build_alt_text(fallback_name, image_idx)
                props.set("descr", generated)
                props.set("title", generated)
                total_updates += 1
                alt_issue = "Missing alt text replaced" if not descr else "Generic alt text replaced"
                result.issues.append(
                    Issue(
                        severity="warning",
                        category="alt_text",
                        message=f"{alt_issue} with '{generated}'.",
                        location=f"slide {slide_idx}, image {image_idx}",
                    )
                )
                result.changes.append(f"Updated alt text on slide {slide_idx} image {image_idx}.")
                result.review_items.append(
                    ReviewItem(
                        review_id=f"pptx-alt-{slide_idx}-{image_idx}",
                        category="alt_text",
                        title="Review slide image alt text",
                        prompt="Confirm the generated alt text or replace it with a content-specific description.",
                        location=f"slide {slide_idx}, image {image_idx}",
                        suggested_value=generated,
                        confidence="medium",
                        priority="high",
                        preview_text=image_review_preview(props.attrib.get("name"), descr, slide_context_hint(root)),
                        secondary_text=f"Suggested alt text: {generated}",
                    )
                )
            audit_slide_structure(root, slide_idx, result)

        apply_pptx_review_actions(slide_docs, result, review_items)

        for slide_path, tree, _root in slide_docs.values():
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
    return generated_fallback_alt_text(name or f"image {index}", index)


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
        visible_text = paragraph_visible_text(paragraph)
        if not visible_text:
            continue

        style = paragraph.find(".//w:pStyle", NS)
        style_val = style.attrib.get(f"{{{NS['w']}}}val", "") if style is not None else ""
        bold_heading = detect_bold_heading(paragraph)
        heading_prompt_text = bold_heading["text"] if bold_heading else visible_text
        if bold_heading or looks_like_heading_candidate(visible_text, style_val):
            heading_candidates += 1
            if heading_candidates <= 5:
                prompt = build_heading_prompt(visible_text, bold_heading)
                result.review_items.append(
                    ReviewItem(
                        review_id=f"docx-heading-{index}",
                        category="heading_structure",
                        title="Check heading structure",
                        prompt=prompt,
                        location=f"paragraph {index}",
                        suggested_value=suggest_heading_level(heading_prompt_text, heading_candidates),
                        confidence="medium",
                        priority="medium",
                        preview_text=visible_text,
                        secondary_text=heading_secondary_text(bold_heading),
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
                    preview_text=visible_text,
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
                preview_text="This slide appears to have no visible text.",
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


def apply_docx_review_actions(root: ET.Element, result: ProcessResult, review_items: list[dict] | None) -> None:
    if not review_items:
        return

    approved = [item for item in review_items if item.get("status") == "approved"]
    if not approved:
        return

    paragraphs = root.findall(".//w:p", NS)
    doc_prs = root.findall(".//wp:docPr", NS)
    hyperlinks = root.findall(".//w:hyperlink", NS)
    tables = root.findall(".//w:tbl", NS)

    for item in approved:
        review_id = item.get("review_id", "")
        suggested_value = (item.get("suggested_value") or "").strip()

        if review_id.startswith("docx-heading-"):
            paragraph_index = parse_review_index(review_id)
            if paragraph_index is None or paragraph_index < 1 or paragraph_index > len(paragraphs):
                continue
            style_value = normalize_heading_style(suggested_value)
            paragraph = paragraphs[paragraph_index - 1]
            heading_signal = detect_bold_heading(paragraph)
            if heading_signal and heading_signal.get("kind") == "lead_in" and split_bold_lead_paragraph(root, paragraph, style_value):
                result.changes.append(f"Split paragraph {paragraph_index} into a heading and body paragraph using {style_value}.")
            else:
                apply_paragraph_style(paragraph, style_value)
                result.changes.append(f"Applied {style_value} to paragraph {paragraph_index} from approved review.")
            continue

        if review_id.startswith("docx-alt-"):
            image_index = parse_review_index(review_id)
            if image_index is None or image_index < 1 or image_index > len(doc_prs):
                continue
            alt_text = suggested_value or doc_prs[image_index - 1].attrib.get("descr", "")
            if not alt_text:
                continue
            doc_prs[image_index - 1].set("descr", alt_text)
            doc_prs[image_index - 1].set("title", alt_text)
            result.changes.append(f"Applied approved alt text for image {image_index}.")
            continue

        if review_id.startswith("docx-link-"):
            link_index = parse_review_index(review_id)
            if link_index is None or link_index < 1 or link_index > len(hyperlinks):
                continue
            if not suggested_value:
                continue
            text_nodes = hyperlinks[link_index - 1].findall(".//w:t", NS)
            replace_text_nodes(text_nodes, suggested_value)
            result.changes.append(f"Applied approved hyperlink text for hyperlink {link_index}.")
            continue

        if review_id.startswith("docx-table-"):
            table_index = parse_review_index(review_id)
            if table_index is None or table_index < 1 or table_index > len(tables):
                continue
            description = suggested_value or get_table_description(tables[table_index - 1])
            if not description:
                continue
            set_table_description(tables[table_index - 1], description)
            result.changes.append(f"Applied approved table description for table {table_index}.")


def apply_pptx_review_actions(
    slide_docs: dict[int, tuple[Path, ET.ElementTree, ET.Element]],
    result: ProcessResult,
    review_items: list[dict] | None,
) -> None:
    if not review_items:
        return

    approved = [item for item in review_items if item.get("status") == "approved"]
    if not approved:
        return

    for item in approved:
        review_id = item.get("review_id", "")
        suggested_value = (item.get("suggested_value") or "").strip()
        if not review_id.startswith("pptx-alt-") or not suggested_value:
            continue
        location = parse_pptx_alt_review_id(review_id)
        if location is None:
            continue
        slide_idx, image_idx = location
        slide_doc = slide_docs.get(slide_idx)
        if slide_doc is None:
            continue
        _slide_path, _tree, root = slide_doc
        shapes = root.findall(".//p:cNvPr", NS)
        if image_idx < 1 or image_idx > len(shapes):
            continue
        shapes[image_idx - 1].set("descr", suggested_value)
        shapes[image_idx - 1].set("title", suggested_value)
        result.changes.append(f"Applied approved alt text for slide {slide_idx} image {image_idx}.")


def parse_review_index(review_id: str) -> int | None:
    match = re.search(r"-(\d+)$", review_id)
    if not match:
        return None
    return int(match.group(1))


def parse_pptx_alt_review_id(review_id: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"pptx-alt-(\d+)-(\d+)", review_id)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def normalize_heading_style(value: str) -> str:
    match = re.search(r"heading\s*([1-6])", value.strip(), flags=re.IGNORECASE)
    if match:
        return f"Heading{match.group(1)}"
    return "Heading2"


def apply_paragraph_style(paragraph: ET.Element, style_value: str) -> None:
    p_pr = paragraph.find("w:pPr", NS)
    if p_pr is None:
        p_pr = ET.Element(f"{{{NS['w']}}}pPr")
        paragraph.insert(0, p_pr)
    p_style = p_pr.find("w:pStyle", NS)
    if p_style is None:
        p_style = ET.SubElement(p_pr, f"{{{NS['w']}}}pStyle")
    p_style.set(f"{{{NS['w']}}}val", style_value)


def replace_text_nodes(text_nodes: list[ET.Element], replacement: str) -> None:
    replaced = False
    for node in text_nodes:
        if not replaced:
            node.text = replacement
            replaced = True
        else:
            node.text = ""


def extract_table_rows(table: ET.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.findall(".//w:tr", NS):
        cells: list[str] = []
        for cell in row.findall("./w:tc", NS):
            cell_text = " ".join(
                text.strip()
                for text in (node.text or "" for node in cell.findall(".//w:t", NS))
                if text.strip()
            )
            cells.append(cell_text)
        if any(cell.strip() for cell in cells):
            rows.append(cells)
    return rows


def get_table_description(table: ET.Element) -> str:
    table_props = table.find("w:tblPr", NS)
    if table_props is None:
        return ""
    for tag in ("w:tblDescription", "w:tblCaption"):
        node = table_props.find(tag, NS)
        if node is not None:
            value = (node.attrib.get(f"{{{NS['w']}}}val") or "").strip()
            if value:
                return value
    return ""


def set_table_description(table: ET.Element, description: str) -> None:
    table_props = table.find("w:tblPr", NS)
    if table_props is None:
        table_props = ET.Element(f"{{{NS['w']}}}tblPr")
        table.insert(0, table_props)
    caption = table_props.find("w:tblCaption", NS)
    if caption is None:
        caption = ET.SubElement(table_props, f"{{{NS['w']}}}tblCaption")
    caption.set(f"{{{NS['w']}}}val", description)
    table_description = table_props.find("w:tblDescription", NS)
    if table_description is None:
        table_description = ET.SubElement(table_props, f"{{{NS['w']}}}tblDescription")
    table_description.set(f"{{{NS['w']}}}val", description)


def image_review_preview(name: str | None, existing_alt_text: str | None, context_hint: str = "") -> str:
    parts = []
    if name and name.strip():
        parts.append(f"Source label: {name.strip()}")
    if context_hint.strip():
        parts.append(f"Nearby context: {context_hint.strip()}")
    if existing_alt_text and existing_alt_text.strip():
        parts.append(f"Current alt text: {existing_alt_text.strip()}")
    return "\n".join(parts)


def table_preview_text(rows: list[list[str]]) -> str:
    preview_lines = []
    for row in rows[:3]:
        cells = [cell.strip() for cell in row[:4] if cell.strip()]
        if cells:
            preview_lines.append(" | ".join(cells))
    return "\n".join(preview_lines)


def slide_context_hint(root: ET.Element) -> str:
    texts = [
        (node.text or "").strip()
        for node in root.findall(".//a:t", NS)
        if (node.text or "").strip()
    ]
    return ". ".join(texts[:12])


def docx_image_context(root: ET.Element, target: ET.Element) -> str:
    paragraph = find_ancestor_tag(root, target, f"{{{NS['w']}}}p")
    body = root.find(".//w:body", NS)
    if paragraph is not None and body is not None:
        siblings = list(body)
        if paragraph in siblings:
            index = siblings.index(paragraph)
            nearby: list[str] = []
            for offset in (-2, -1, 1, 2, 3):
                neighbor_index = index + offset
                if neighbor_index < 0 or neighbor_index >= len(siblings):
                    continue
                neighbor = siblings[neighbor_index]
                if neighbor.tag != f"{{{NS['w']}}}p":
                    continue
                neighbor_text = paragraph_visible_text(neighbor)
                if neighbor_text:
                    nearby.append(neighbor_text)
            if nearby:
                return ". ".join(nearby[:4])

    if paragraph is not None:
        paragraph_text = paragraph_visible_text(paragraph)
        if paragraph_text:
            return paragraph_text
    paragraphs = [paragraph_visible_text(p) for p in root.findall(".//w:p", NS)]
    context = [text for text in paragraphs if text][:4]
    return ". ".join(context)


def build_heading_prompt(visible_text: str, bold_heading: dict | None) -> str:
    if not bold_heading:
        return f"This paragraph looks like a heading visually. Verify that a true heading style is applied. Paragraph text: '{visible_text}'."
    if bold_heading.get("kind") == "lead_in":
        return (
            "This paragraph begins with bold text that looks like a section header. "
            f"If approved, the bold lead-in will be split into its own heading. Lead-in text: '{bold_heading['text']}'."
        )
    return (
        "This paragraph appears to be a standalone bold header. "
        f"If approved, the full paragraph will receive a heading style. Header text: '{bold_heading['text']}'."
    )


def heading_secondary_text(bold_heading: dict | None) -> str:
    if not bold_heading:
        return ""
    if bold_heading.get("kind") == "lead_in":
        return f"Bold lead-in detected: {bold_heading['text']}"
    return f"Standalone bold header detected: {bold_heading['text']}"


def paragraph_visible_text(paragraph: ET.Element) -> str:
    texts = [node.text or "" for node in paragraph.findall(".//w:t", NS)]
    return "".join(texts).strip()


def detect_bold_heading(paragraph: ET.Element) -> dict | None:
    runs = paragraph.findall("./w:r", NS)
    if not runs:
        return None

    text_runs: list[tuple[ET.Element, str, bool]] = []
    for run in runs:
        text_value = run_visible_text(run)
        if text_value.strip():
            text_runs.append((run, text_value, run_is_bold(run)))
    if not text_runs:
        return None

    leading_runs: list[tuple[ET.Element, str, bool]] = []
    for run, text_value, is_bold in text_runs:
        if is_bold:
            leading_runs.append((run, text_value, is_bold))
            continue
        break

    if leading_runs and len(leading_runs) < len(text_runs):
        heading_text = "".join(text_value for _run, text_value, _is_bold in leading_runs).strip()
        if valid_heading_text(heading_text):
            return {"kind": "lead_in", "text": heading_text}

    if all(is_bold for _run, _text_value, is_bold in text_runs):
        heading_text = "".join(text_value for _run, text_value, _is_bold in text_runs).strip()
        if valid_heading_text(heading_text):
            return {"kind": "standalone", "text": heading_text}

    bold_ratio = sum(len(text_value.strip()) for _run, text_value, is_bold in text_runs if is_bold) / max(
        sum(len(text_value.strip()) for _run, text_value, _is_bold in text_runs),
        1,
    )
    if bold_ratio >= 0.65:
        heading_text = "".join(text_value for _run, text_value, _is_bold in text_runs).strip()
        if valid_heading_text(heading_text):
            return {"kind": "standalone", "text": heading_text}
    return None


def valid_heading_text(heading_text: str) -> bool:
    if not heading_text:
        return False
    compact = heading_text.strip()
    word_count = len(compact.replace(":", " ").split())
    if word_count == 0 or word_count > 12 or len(compact) > 90:
        return False
    if re.fullmatch(r"[()\[\]{}0-9.\-\s]+", compact):
        return False
    return True


def run_visible_text(run: ET.Element) -> str:
    return "".join(node.text or "" for node in run.findall(".//w:t", NS))


def run_is_bold(run: ET.Element) -> bool:
    bold = run.find(".//w:b", NS)
    if bold is None:
        return False
    val = bold.attrib.get(f"{{{NS['w']}}}val", "1").lower()
    return val not in {"0", "false", "off"}


def split_bold_lead_paragraph(root: ET.Element, paragraph: ET.Element, style_value: str) -> bool:
    heading_signal = detect_bold_heading(paragraph)
    if not heading_signal or heading_signal.get("kind") != "lead_in":
        return False
    heading_text = heading_signal["text"]

    runs = paragraph.findall("./w:r", NS)
    lead_runs: list[ET.Element] = []
    for run in runs:
        text = run_visible_text(run)
        if not text.strip():
            if lead_runs:
                lead_runs.append(run)
            continue
        if run_is_bold(run):
            lead_runs.append(run)
            continue
        break

    remaining_text = paragraph_visible_text(paragraph)
    if remaining_text == heading_text:
        return False

    new_paragraph = ET.Element(f"{{{NS['w']}}}p")
    apply_paragraph_style(new_paragraph, style_value)
    for run in lead_runs:
        new_paragraph.append(copy.deepcopy(run))

    parent, child_index = find_parent_with_index(root, paragraph)
    if parent is None or child_index is None:
        return False

    for run in lead_runs:
        if run in list(paragraph):
            paragraph.remove(run)
    trim_leading_whitespace_runs(paragraph)
    if not paragraph_visible_text(paragraph):
        return False

    parent.insert(child_index, new_paragraph)
    return True


def trim_leading_whitespace_runs(paragraph: ET.Element) -> None:
    for run in paragraph.findall("./w:r", NS):
        text_nodes = run.findall(".//w:t", NS)
        if not text_nodes:
            continue
        for node in text_nodes:
            if node.text:
                node.text = node.text.lstrip()
                if node.text:
                    return
        if run_visible_text(run).strip():
            return


def find_ancestor_tag(root: ET.Element, child: ET.Element, tag: str) -> ET.Element | None:
    parent = find_parent(root, child)
    while parent is not None:
        if parent.tag == tag:
            return parent
        parent = find_parent(root, parent)
    return None


def find_parent(root: ET.Element, child: ET.Element) -> ET.Element | None:
    for parent in root.iter():
        for candidate in list(parent):
            if candidate is child:
                return parent
    return None


def find_parent_with_index(root: ET.Element, child: ET.Element) -> tuple[ET.Element | None, int | None]:
    for parent in root.iter():
        children = list(parent)
        for index, candidate in enumerate(children):
            if candidate is child:
                return parent, index
    return None, None


def looks_like_heading_candidate(visible_text: str, style_val: str) -> bool:
    if style_val.lower().startswith("heading"):
        return False
    if len(visible_text) > 90:
        return False
    if visible_text.count(".") > 1:
        return False
    words = visible_text.split()
    if len(words) > 12:
        return False
    uppercase_ratio = sum(1 for char in visible_text if char.isupper()) / max(len([c for c in visible_text if c.isalpha()]), 1)
    title_case_like = visible_text == visible_text.title()
    return uppercase_ratio > 0.3 or title_case_like or visible_text.endswith(":")


def suggest_heading_level(visible_text: str, heading_candidates: int) -> str:
    text = visible_text.strip()
    word_count = len(text.split())
    if heading_candidates == 1 and word_count <= 6 and not text.endswith(":"):
        return "Heading 1"
    if text.endswith(":") or word_count >= 8:
        return "Heading 3"
    return "Heading 2"


def should_replace_existing_alt_text(value: str) -> bool:
    normalized = normalize_alt_text(value)
    if not normalized:
        return True
    generic_markers = (
        "description automatically generated",
        "a picture containing",
        "image may contain",
        "screenshot of",
        "photo of text",
        "clip art",
    )
    if any(marker in normalized for marker in generic_markers):
        return True
    if normalized.startswith("describe this image"):
        return True
    return False


def normalize_alt_text(value: str) -> str:
    compact = re.sub(r"\s+", " ", value.strip().lower())
    return compact


def build_audit_summary(result: ProcessResult) -> AuditSummary:
    manual_checks = sum(1 for issue in result.issues if issue.severity == "info")
    needs_review = len(result.review_items)
    auto_applied = len(result.changes)
    score = max(40, min(100, 100 - needs_review * 6 - manual_checks * 4))
    return AuditSummary(
        score=score,
        auto_applied=auto_applied,
        needs_review=needs_review,
        manual_checks=manual_checks,
    )
