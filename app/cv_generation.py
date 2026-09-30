import re
import unicodedata
from io import BytesIO

from docx import Document

from .scoring import build_ats_result


WORD_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MISSION_EMAIL = "boubacar@osito-solution.com"


def _safe_filename(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(character for character in normalized if not unicodedata.combining(character))
    return re.sub(r"[^A-Za-z0-9]+", "_", ascii_value).strip("_")[:80] or "Mission"


def targeted_cv_filename(opportunity, extension: str = "docx") -> str:
    label = _safe_filename(opportunity.title)
    company = _safe_filename(opportunity.company) if opportunity.company else ""
    suffix = f"_{company}" if company else ""
    return f"Boubacar_DIABY_{label}{suffix}.{extension}"


def _replace_text_runs(paragraph, value: str) -> None:
    """Replace visible text while preserving drawing-only runs such as the CV photo."""
    text_runs = [run for run in paragraph.runs if run.text]
    if not text_runs:
        paragraph.add_run(value)
        return
    text_runs[0].text = value
    for run in text_runs[1:]:
        run.text = ""


def _headline(ats: dict) -> str:
    keywords = []
    for keyword in ats["matched_keywords"]:
        label = keyword.upper() if keyword in {"api", "crm", "cpq", "lwc", "soql"} else keyword.title()
        if label not in keywords:
            keywords.append(label)
    return " | ".join(keywords[:4]) or "Salesforce | Architecture CRM | Delivery"


def build_targeted_cv(master_content: bytes, opportunity, profile) -> bytes:
    """Create a tailored copy of the master CV without changing the stored original."""
    document = Document(BytesIO(master_content))
    # Internal assessments (for example AssessFirst notes) may inform the
    # private ATS analysis, but must never be copied verbatim into a CV.
    ats = build_ats_result(opportunity, profile, include_soft_skill_profile=False)
    visible_paragraphs = [paragraph for paragraph in document.paragraphs if paragraph.text.strip()]
    if not visible_paragraphs:
        raise ValueError("Le CV maître ne contient aucun paragraphe exploitable")

    title = opportunity.title.strip() or ats["suggested_title"]
    _replace_text_runs(visible_paragraphs[0], f"{title}\n{_headline(ats)}")

    for paragraph in visible_paragraphs[:10]:
        for run in paragraph.runs:
            if "@" in run.text:
                run.text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", MISSION_EMAIL, run.text)

    summary_paragraph = next(
        (
            paragraph
            for paragraph in visible_paragraphs[1:12]
            if len(paragraph.text.strip()) >= 120 and paragraph.text.strip() != paragraph.text.strip().upper()
        ),
        None,
    )
    if summary_paragraph is not None:
        _replace_text_runs(summary_paragraph, ats["tailored_summary"])

    document.core_properties.title = f"CV ciblé — {title}"
    document.core_properties.subject = opportunity.company or "Mission Salesforce"
    document.core_properties.keywords = ", ".join(ats["matched_keywords"])

    output = BytesIO()
    document.save(output)
    return output.getvalue()
