from types import SimpleNamespace
from app.scoring import build_ats_result, score_opportunity


def profile(**kwargs):
    defaults={"title":"Architecte Salesforce","summary":"Architecture et delivery CRM","skills":["Salesforce","Service Cloud","MuleSoft"],"preferred_roles":["Architecte Salesforce"],"preferred_locations":["Paris"],"minimum_daily_rate":700,"cv_text":"Architecture Salesforce, Service Cloud et intégrations MuleSoft"}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def opportunity(**kwargs):
    defaults={"title":"Architecte Salesforce","description":"Architecture Service Cloud et intégration MuleSoft","location":"Paris","work_mode":"Hybride","daily_rate":750}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_strong_match_scores_high():
    score, details=score_opportunity(opportunity(),profile())
    assert score>=80
    assert "Salesforce" in details["competences"]["matches"]


def test_low_rate_reduces_rate_points():
    _, details=score_opportunity(opportunity(daily_rate=350),profile())
    assert details["tjm"]["points"]==5


def test_ats_never_adds_missing_skills_to_summary():
    result=build_ats_result(opportunity(description="Salesforce CPQ et Omnistudio"),profile())
    assert "cpq" in result["missing_keywords"]
    assert "cpq" not in result["tailored_summary"].lower()

