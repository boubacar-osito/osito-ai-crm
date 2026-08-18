import re
import unicodedata


SALESFORCE_CORE = {
    "salesforce", "service cloud", "sales cloud", "experience cloud", "community cloud",
    "marketing cloud", "mulesoft", "apex", "lwc", "lightning", "soql", "crm",
    "cpq", "omnistudio", "integration", "architecture", "architecte", "devops",
}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.lower().replace("’", "'").replace("‘", "'"))
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


def score_lead(lead) -> tuple[int, dict]:
    # Free-form notes are deliberately excluded: a phrase such as
    # "IT à confirmer" must not count as positive evidence.
    text = normalize(f"{lead.headline} {lead.company}")
    commercial_terms = ("ingenieur d'affaire", "charge d'affaire", "chargee d'affaire", "account manager", "business manager", "business developer")
    talent_terms = ("recrutement", "recruteur", "recruteuse", "talent acquisition", "ressources humaines", "specialiste rh")
    leadership_terms = ("fondatrice", "fondateur", "co-fondatrice", "co-fondateur", "directrice generale", "directeur general", "dirigeante", "dirigeant", "gerante", "gerant")
    intermediary_terms = ("consulting", "agency", "cabinet", "conseil")
    crm_terms = ("salesforce", "crm", "erp crm", "practice salesforce")
    it_terms = (" it ", "esn", "digital", "tech", "informatique", "davidson", "sariel", "guarani", "profila")
    access_terms = ("portage", "freelance", "recrutement", "staffing", "ingenieur d'affaire", "business manager", "charge d'affaire")

    commercial_matches = [term for term in commercial_terms if normalize(term) in text]
    talent_matches = [term for term in talent_terms if normalize(term) in text]
    leadership_matches = [term for term in leadership_terms if normalize(term) in text]
    intermediary_matches = [term for term in intermediary_terms if normalize(term) in text]
    crm_matches = [term for term in crm_terms if normalize(term) in text]
    it_matches = [term for term in it_terms if normalize(term) in text]
    access_matches = [term for term in access_terms if normalize(term) in text]
    qualified_talent_intermediary = bool(talent_matches and (it_matches or intermediary_matches))
    qualified_firm_leader = bool(leadership_matches and it_matches and intermediary_matches)
    role_matches = sorted(set(commercial_matches + talent_matches + leadership_matches))

    role_points = 30 if (commercial_matches or qualified_talent_intermediary or qualified_firm_leader) else (20 if "responsable fonctionnel" in text else 0)
    ecosystem_points = 30 if crm_matches else (25 if it_matches else (15 if "openwork" in text or "experconnect" in text else 0))
    if access_matches or qualified_talent_intermediary or qualified_firm_leader:
        access_points = 25
        access_matches = sorted(set(access_matches + intermediary_matches + leadership_matches))
    else:
        access_points = 20 if crm_matches and "responsable" in text else 0
    recency_points = 10 if lead.connected_on else 0
    score = min(100, role_points + ecosystem_points + access_points + recency_points)
    priority = "haute" if score >= 75 else ("moyenne" if score >= 50 else "faible")
    return score, {
        "priorite": priority,
        "role_commercial": {"points": role_points, "matches": role_matches},
        "ecosysteme_it_crm": {"points": ecosystem_points, "matches": sorted(set(crm_matches + it_matches))},
        "acces_aux_missions": {"points": access_points, "matches": access_matches},
        "connexion_recente": {"points": recency_points},
    }
