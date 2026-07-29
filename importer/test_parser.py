"""
Unit tests for the pipe-header parser in fetch_jobs.py.

Each test builds a minimal HN item dict (only id, time, text are used by
parse_job) from a literal first-line string, then asserts the structured
fields extracted by the token-classification heuristic.
"""

import pytest
from fetch_jobs import parse_job


def make_item(first_line: str, body: str = "", item_id: int = 1) -> dict:
    text = first_line + ("<p>" + body if body else "")
    return {"id": item_id, "text": text, "time": 1751000000}


# ---------------------------------------------------------------------------
# Regression: cases that were already working before the classifier rewrite
# ---------------------------------------------------------------------------

class TestRegressionWorkingCases:
    def test_classic_company_title_remote_salary(self):
        job = parse_job(make_item("Acme Corp | Senior Engineer | Remote | $150k–$180k"))
        assert job["company"] == "Acme Corp"
        assert job["title"] == "Senior Engineer"
        assert job["location"] is None
        assert job["is_remote"] is True

    def test_location_onsite_before_role(self):
        job = parse_job(make_item(
            "Prior Labs | Berlin / Freiburg / NYC | ONSITE | Full-time | Multiple Roles"
            " | https://priorlabs.ai"
        ))
        assert job["company"] == "Prior Labs"
        assert job["location"] == "Berlin / Freiburg / NYC"
        assert job["is_remote"] is False

    def test_remote_phrase_not_assigned_to_title(self):
        job = parse_job(make_item(
            "ALBERT | REMOTE ALMOST ANYWHERE IN THE WORLD"
            " | Hiring principal and distinguished engineers to build net new products"
        ))
        assert job["company"] == "ALBERT"
        assert job["title"] is not None
        assert "engineer" in job["title"].lower()
        assert job["is_remote"] is True

    def test_stjude_title_and_city_state_location(self):
        job = parse_job(make_item(
            "St. Jude Children's Research Hospital"
            " | Sr. Staff or Principal Software Engineer, Rust Genomics Infrastructure"
            " | Memphis, TN | Remote"
        ))
        assert job["company"] == "St. Jude Children's Research Hospital"
        assert job["title"] == "Sr. Staff or Principal Software Engineer, Rust Genomics Infrastructure"
        assert job["location"] == "Memphis, TN"
        assert job["is_remote"] is True

    def test_remote_with_geo_qualifier_kept_as_location(self):
        job = parse_job(make_item(
            "Conservation Metrics | Lead Software Engineer, Data Sovereignty | REMOTE (US)"
            " | https://conservationmetrics.com/careers/lead-data-platform-engineer/"
        ))
        assert job["company"] == "Conservation Metrics"
        assert job["title"] == "Lead Software Engineer, Data Sovereignty"
        assert job["location"] == "REMOTE (US)"
        assert job["is_remote"] is True


# ---------------------------------------------------------------------------
# New failing cases — these drove the classifier improvements
# ---------------------------------------------------------------------------

class TestBareDomainsNotLocation:
    def test_bare_domain_skipped(self):
        """theartistbreakfast.com in a pipe slot must not be captured as location."""
        job = parse_job(make_item(
            "BREAKFAST Studio | Senior Technical Program Manager | theartistbreakfast.com"
        ))
        assert job["company"] == "BREAKFAST Studio"
        assert job["title"] == "Senior Technical Program Manager"
        assert job["location"] is None

    def test_bare_dotio_domain_skipped(self):
        job = parse_job(make_item("Startup | Backend Engineer | startup.io"))
        assert job["title"] == "Backend Engineer"
        assert job["location"] is None

    def test_url_with_path_already_skipped(self):
        """Full URLs (with path) are already filtered; this is a sanity check."""
        job = parse_job(make_item(
            "Acme | Software Engineer | https://acme.com/jobs/swe"
        ))
        assert job["title"] == "Software Engineer"
        assert job["location"] is None


class TestFoundingEngineers:
    def test_founding_engineers_is_title(self):
        """'Founding Engineers' should be classified as title, not location."""
        job = parse_job(make_item(
            "Talk Machine | Founding Engineers | Remote | https://talkmachine.com/jobs/engineer"
        ))
        assert job["company"] == "Talk Machine"
        assert job["title"] == "Founding Engineers"
        assert job["is_remote"] is True

    def test_founding_engineer_singular(self):
        job = parse_job(make_item("Startup | Founding Engineer | Remote"))
        assert job["title"] == "Founding Engineer"

    def test_engineers_plural_matches(self):
        """Plural 'Engineers' must match the title classifier."""
        job = parse_job(make_item("Startup | SF | Senior Engineers | Full-time"))
        assert job["title"] == "Senior Engineers"
        assert job["location"] == "SF"


class TestSWEFirmwareMechanical:
    def test_principal_mechanical_firmware_swe(self):
        """SWE / Firmware / Mechanical must be recognized as role keywords."""
        job = parse_job(make_item(
            "Robotics startup (STEALTH + YC S26)"
            " | Principal Mechanical + Firmware + SWE | SF Bay Area"
        ))
        assert job["company"] == "Robotics startup (STEALTH + YC S26)"
        assert job["title"] == "Principal Mechanical + Firmware + SWE"
        assert job["location"] == "SF Bay Area"

    def test_swe_standalone(self):
        job = parse_job(make_item("Startup | NYC | SWE | Full-time"))
        assert job["title"] == "SWE"
        assert job["location"] == "NYC"

    def test_firmware_engineer(self):
        job = parse_job(make_item("Startup | Austin, TX | Firmware Engineer"))
        assert job["title"] == "Firmware Engineer"
        assert job["location"] == "Austin, TX"

    def test_mechanical_engineer(self):
        job = parse_job(make_item("Hardware Co | Boston | Mechanical Engineer"))
        assert job["title"] == "Mechanical Engineer"
        assert job["location"] == "Boston"
