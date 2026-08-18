import re
import unicodedata


SALESFORCE_CORE = {
    "salesforce", "service cloud", "sales cloud", "experience cloud", "community cloud",
    "marketing cloud", "mulesoft", "apex", "lwc", "lightning", "soql", "crm",
    "cpq", "omnistudio", "integration", "architecture", "architecte", "devops",
}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.lower())
    return "".join(c for c in value if not unicodedata.combining(c))


def contains(text: str, term: str) -> bool:
    return normalize(term) in normalize(text)


def score_opportunity(opportunity, profile) -> tuple[int, dict]:
    text = f"{opportunity.title} {opportunity.description} {opportunity.location} {opportunity.work_mode}"
    profile_skills = [s.strip() for s in profile.skills if s.strip()]
    matched_skills = [skill for skill in profile_skills if contains(text, skill)]
    skill_score = round(45 * len(matched_skills) / max(1, min(len(profile_skills), 10)))

    matched_roles = [role for role in profile.preferred_roles if contains(opportunity.title, role)]
    role_score = 20 if matched_roles else 0

    sf_terms = sorted(term for term in SALESFORCE_CORE if contains(text, term))
    ecosystem_score = min(15, len(sf_terms) * 3)

    location_match = any(contains(text, loc) for loc in profile.preferred_locations)
    location_score = 10 if location_match or not profile.preferred_locations else 0

    rate_score = 0
    if opportunity.daily_rate and profile.minimum_daily_rate:
        rate_score = 10 if opportunity.daily_rate >= profile.minimum_daily_rate else max(
            0, round(10 * opportunity.daily_rate / profile.minimum_daily_rate)
        )
    elif not profile.minimum_daily_rate:
        rate_score = 10

    score = min(100, skill_score + role_score + ecosystem_score + location_score + rate_score)
    details = {
        "competences": {"points": skill_score, "matches": matched_skills},
        "role": {"points": role_score, "matches": matched_roles},
        "ecosysteme_salesforce": {"points": ecosystem_score, "matches": sf_terms},
        "localisation": {"points": location_score, "match": location_match},
        "tjm": {"points": rate_score, "mission": opportunity.daily_rate, "minimum": profile.minimum_daily_rate},
    }
    return score, details


def extract_keywords(text: str) -> list[str]:
    candidates = SALESFORCE_CORE | {
        "agile", "scrum", "safe", "api", "rest", "soap", "ci/cd", "jira", "anglais",
        "lead", "delivery", "migration", "data", "fonctionnel", "technique", "freelance",
    }
    return sorted(term for term in candidates if contains(text, term))


def build_ats_result(opportunity, profile) -> dict:
    job_keywords = extract_keywords(f"{opportunity.title} {opportunity.description}")
    cv_source = f"{profile.title} {profile.summary} {' '.join(profile.skills)} {profile.cv_text}"
    matched = [kw for kw in job_keywords if contains(cv_source, kw)]
    missing = [kw for kw in job_keywords if kw not in matched]
    match_score = round(100 * len(matched) / max(1, len(job_keywords)))
    ordered = sorted(profile.skills, key=lambda skill: (not contains(opportunity.description, skill), skill.lower()))
    highlights = ", ".join(matched[:6]) or "expertise Salesforce"
    summary = profile.summary.strip()
    if summary:
        tailored = f"{profile.title or 'Consultant Salesforce'} — {summary} Compétences particulièrement pertinentes pour cette mission : {highlights}."
    else:
        tailored = f"{profile.title or 'Consultant Salesforce'} avec une expertise pertinente en {highlights}."
    return {
        "match_score": match_score,
        "matched_keywords": matched,
        "missing_keywords": missing,
        "suggested_title": opportunity.title,
        "tailored_summary": tailored,
        "reordered_skills": ordered,
        "warnings": [
            "Les mots-clés manquants ne doivent être ajoutés que s'ils correspondent à une expérience réelle.",
            "Relire et valider chaque adaptation avant envoi.",
        ],
    }

