"""Audit MissionFlow opportunity URLs and enrich publication dates.

Run inside the production container. Without --apply this script is read-only.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import re
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlparse

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Opportunity


USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36"
LINKEDIN_ACTIVITY = re.compile(r"(?:activity-|share-|ugcPost-)(\d{16,20})", re.IGNORECASE)
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
UNAVAILABLE_PHRASES = (
    "no longer accepting applications",
    "cette offre n’est plus disponible",
    "cette offre n'est plus disponible",
    "cette mission n’est plus disponible",
    "cette mission n'est plus disponible",
    "job is no longer available",
    "offre expirée",
    "mission expirée",
)


def linkedin_publication_date(url: str) -> date | None:
    match = LINKEDIN_ACTIVITY.search(url)
    if not match:
        return None
    timestamp_ms = int(match.group(1)) >> 22
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).date()


def iter_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json(child)


def structured_publication_date(html: str) -> date | None:
    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in blocks:
        try:
            parsed = json.loads(block.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        for item in iter_json(parsed):
            for key in ("datePosted", "datePublished"):
                raw = str(item.get(key, ""))
                match = ISO_DATE.match(raw)
                if match:
                    return date.fromisoformat(match.group(0))
    for pattern in (
        r'"datePosted"\s*:\s*"(\d{4}-\d{2}-\d{2})',
        r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})',
    ):
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            return date.fromisoformat(match.group(1))
    return None


def fetch(url: str) -> tuple[int | None, str, str, str]:
    if not url:
        return None, "", "", "no_source_url"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7"})
    try:
        with urllib.request.urlopen(request, timeout=25, context=ssl.create_default_context()) as response:
            body = response.read(2_000_000).decode("utf-8", "ignore")
            return response.status, response.geturl(), body, ""
    except urllib.error.HTTPError as exc:
        body = exc.read(500_000).decode("utf-8", "ignore")
        return exc.code, exc.geturl(), body, f"http_{exc.code}"
    except Exception as exc:  # Network/access errors are never treated as proof of closure.
        return None, url, "", f"{type(exc).__name__}: {exc}"


def availability(url: str, status: int | None, final_url: str, html: str) -> tuple[str, str]:
    if not url:
        return "unverified", "Lien source absent"
    if status in {404, 410}:
        return "unavailable", f"HTTP {status}"
    if "linkedin.com" in url and "expired_jd_redirect" in final_url:
        return "unavailable", "L’offre LinkedIn redirige vers la recherche des offres expirées"
    if "linkedin.com" in url and any(
        marker in final_url for marker in ("/uas/login", "/signup/cold-join")
    ):
        return "unverified", "La page LinkedIn exige une authentification"
    host = urlparse(url).netloc.lower()
    final_path = urlparse(final_url).path.rstrip("/")
    if "free-work.com" in host and final_path in {"/fr/tech-it/jobs", "/fr/tech-it/job-mission"}:
        return "unavailable", "La page mission redirige vers la liste générale Free-Work"
    normalized = re.sub(r"\s+", " ", html.lower())
    for phrase in UNAVAILABLE_PHRASES:
        if phrase in normalized:
            return "unavailable", f"La source indique : {phrase}"
    if status == 200:
        return "available", "Page source accessible"
    return "unverified", "Disponibilité non vérifiable automatiquement"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = []
    with SessionLocal() as db:
        opportunities = db.scalars(select(Opportunity).order_by(Opportunity.id)).all()
        for item in opportunities:
            exact_date = linkedin_publication_date(item.source_url)
            status, final_url, html, error = fetch(item.source_url)
            exact_date = exact_date or structured_publication_date(html)
            state, reason = availability(item.source_url, status, final_url, html)
            publication = exact_date or item.created_at.date()
            estimated = exact_date is None
            previous_stage = item.stage
            if args.apply:
                item.published_on = publication
                item.published_on_is_estimated = estimated
                if state == "unavailable" and item.stage not in {"perdue", "gagnee"}:
                    item.stage = "perdue"
            report.append({
                "id": item.id,
                "title": item.title,
                "source_url": item.source_url,
                "http_status": status,
                "final_url": final_url,
                "availability": state,
                "reason": reason,
                "fetch_error": error,
                "published_on": publication.isoformat(),
                "published_on_is_estimated": estimated,
                "previous_stage": previous_stage,
                "resulting_stage": item.stage,
            })
        if args.apply:
            db.commit()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
