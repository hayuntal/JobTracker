"""The matcher decides what reaches your phone, so it gets the most coverage.

Every test runs against the real config/search.yaml rather than a fixture: the
point is to catch a config edit that quietly stops matching your roles, which a
hand-built fixture would hide.
"""
import pytest

from jobtracker.config import load_search
from jobtracker.matching import Matcher, detect_seniority
from jobtracker.models import Job


@pytest.fixture(scope="module")
def matcher():
    return Matcher(load_search())


def job(title, location="Tel Aviv-Yafo", company="Test Co", description=""):
    return Job(title=title, company=company, location=location, url="",
               source="test", description=description)


# -- titles -----------------------------------------------------------------

@pytest.mark.parametrize("title,role", [
    ("Data Engineer", "Data Engineer"),
    ("Analytics Engineer", "Data Engineer"),
    ("BI Developer", "Data Engineer"),
    ("Business Intelligence Engineer", "Data Engineer"),
    ("Data Analyst", "Data Analyst"),
    ("Product Analyst", "Data Analyst"),
    ("Data Scientist", "Data Scientist"),
    ("Machine Learning Engineer", "Data Scientist"),
    ("AI Engineer", "Data Scientist"),
    ("Algorithm Engineer", "Data Scientist"),
    ("Research Scientist", "Data Scientist"),
    ("מהנדס נתונים", "Data Engineer"),
    ("אנליסט דאטה", "Data Analyst"),
])
def test_target_titles_match(matcher, title, role):
    ok, reason = matcher.evaluate(job(title))
    assert ok, reason
    assert matcher.roles[matcher.match_title(title)[0]]["label"] == role


@pytest.mark.parametrize("title", [
    "Data Center Technician",     # 'data' but not the job
    "Data Entry Clerk",
    "Financial Analyst",
    "Security Analyst",
    "Backend Engineer",
    "Product Manager",
    "Business Analyst (GTM)",     # requirements role, not a data one
    "Senior Analyst, Procurement",
])
def test_non_target_titles_rejected(matcher, title):
    ok, _ = matcher.evaluate(job(title))
    assert not ok


@pytest.mark.parametrize("title", [
    "Senior Data Engineer",
    "Sr. Data Analyst",
    "Staff ML Engineer",
    "Principal Product Analyst",
    "Data Scientist Expert",
    "Head of Data Engineer Group",
    "VP Data Analyst",
])
def test_senior_and_above_are_filtered(matcher, title):
    """The title gate accepts these; the seniority gate is what drops them."""
    assert matcher.match_title(title)[0] is not None
    ok, reason = matcher.evaluate(job(title))
    assert not ok
    assert "seniority" in reason


@pytest.mark.parametrize("title", [
    "Junior Data Analyst",
    "Data Engineer",
    "Data Analyst",
    "ML Engineer",
    "Data Engineer Team Lead",    # 'lead' is deliberately not filtered
])
def test_junior_and_mid_still_pass(matcher, title):
    ok, reason = matcher.evaluate(job(title))
    assert ok, reason


def test_field_and_gtm_ai_engineers_are_excluded(matcher):
    """`ai engineer` is a target, but the sales-side variants are not."""
    for title in ("Field AI Engineer", "GTM AI Engineer"):
        ok, reason = matcher.evaluate(job(title))
        assert not ok
        assert "excluded title" in reason


def test_word_boundaries_are_respected(matcher):
    """'data engineering manager' must not be matched by a substring hit."""
    assert matcher.match_title("Databases Engineer")[0] is None


def test_most_specific_pattern_wins(matcher):
    _, pattern = matcher.match_title("Junior Data Analyst")
    assert pattern == "junior data analyst"


# -- locations --------------------------------------------------------------

@pytest.mark.parametrize("location", [
    "Tel Aviv-Yafo, Israel", "Herzliya", "Haifa", "Israel", "ישראל",
    "Petah Tikva, Israel", "Ra'anana",
])
def test_israeli_locations_accepted(matcher, location):
    accepted, _ = matcher.match_location(location)
    assert accepted


@pytest.mark.parametrize("location", [
    "New York, NY", "London, UK", "Remote - United States", "Bangalore, India",
    "Remote - US", "Remote, North America", "US - Remote",
])
def test_foreign_locations_rejected(matcher, location):
    accepted, _ = matcher.match_location(location)
    assert not accepted


def test_us_reject_does_not_misfire_on_israeli_cities(matcher):
    """'us' is a whole-word pattern, so 'Jerusalem' must still be accepted."""
    assert matcher.match_location("Jerusalem, Israel")[0]
    assert matcher.match_location("Remote - Jerusalem")[0]


def test_remote_emea_accepted(matcher):
    accepted, remote = matcher.match_location("Remote - EMEA")
    assert accepted and remote


def test_bare_remote_needs_the_description(matcher):
    """Bare 'Remote' is usually US-scoped, so it must not pass on its own."""
    assert not matcher.match_location("Remote")[0]
    assert matcher.needs_description(job("Data Engineer", location="Remote"))
    assert matcher.match_location("Remote", "The team sits in Tel Aviv.")[0]


def test_description_is_not_fetched_when_location_is_clear(matcher):
    assert not matcher.needs_description(job("Data Engineer", location="Haifa, Israel"))
    assert not matcher.needs_description(job("Data Engineer", location="Remote - US"))


# -- annotation -------------------------------------------------------------

def test_accepted_job_is_annotated(matcher):
    j = job("Junior Data Engineer", location="Remote - Israel")
    assert matcher.evaluate(j)[0]
    assert j.matched_role == "Data Engineer"
    assert j.seniority == "junior"
    assert j.is_remote is True


@pytest.mark.parametrize("title,level", [
    ("Senior Data Analyst", "senior"),
    ("Junior Data Scientist", "junior"),
    ("Data Engineering Team Lead", "lead"),
    ("Student Data Analyst", "intern"),
    ("Data Engineer", None),
])
def test_seniority_detection(title, level):
    assert detect_seniority(title) == level


def test_filter_jobs_keeps_only_matches(matcher):
    jobs = [job("Data Engineer"), job("Chef"), job("Data Analyst", location="Berlin")]
    assert [j.title for j in matcher.filter_jobs(jobs)] == ["Data Engineer"]
