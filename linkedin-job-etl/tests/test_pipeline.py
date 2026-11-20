"""Run with:  pytest -q   (from the project folder)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

import cleaner
import scraper
import config
from cleaner import standardize_role, parse_location, clean_and_transform, build_skills_long
from demo_data import generate_raw_jobs


# ---------------- cleaner ----------------
def test_role_no_false_ai_match():
    assert standardize_role("Data Analyst - Navi Mumbai") == "Data Analyst"
    assert standardize_role("Data Analyst Thai Market") == "Data Analyst"
    assert standardize_role("AI Engineer") == "Data Scientist"
    assert standardize_role("Associate Analyst, Analytics & Metrics") == "Data Analyst"
    assert standardize_role("Power BI Developer") == "BI Developer"
    assert standardize_role("Office Manager") == "Other Analytical Role"
    assert standardize_role(None) == "Other"


def test_parse_location():
    assert parse_location("Mumbai, Maharashtra, India") == ("Mumbai", "Maharashtra", "India")
    assert parse_location("Bangalore, Karnataka, India")[0] == "Bengaluru"
    assert parse_location("India") == ("Unknown", "Unknown", "India")
    assert parse_location("Mumbai, India") == ("Mumbai", "Unknown", "India")
    assert parse_location(None) == ("Unknown", "Unknown", "Unknown")


def _raw(**over):
    base = dict(job_id="1", search_keyword="Data Analyst", search_location="Mumbai",
                job_title="Data Analyst", company_name="Acme", location="Mumbai, Maharashtra, India",
                posted_date="2026-11-05", job_url=None, job_description="We need SQL and Excel.",
                seniority_level="Not Applicable", employment_type=None, job_function=None, industries=None)
    base.update(over)
    return base


def test_clean_flags_and_no_invented_data():
    raw = pd.DataFrame([
        _raw(),
        _raw(job_id="2", job_title="BI Dev", company_name="B", posted_date=None, job_description=None,
             location="Mumbai Metropolitan Region"),
    ])
    out = clean_and_transform(raw)
    a, b = out.iloc[0], out.iloc[1]
    assert a["has_sql"] and a["has_excel"] and not a["has_python"]
    assert a["seniority_level"] == "Not Specified"          # "Not Applicable" normalised
    assert a["employment_type"] == "Not Specified"          # not invented as Full-time
    assert bool(a["description_fetched"]) and not bool(b["description_fetched"])
    assert b["extracted_skills"] == "Description Unavailable"
    assert pd.isna(b["posted_date"])                         # NOT replaced by today's date
    assert b["metro_area"] == "Mumbai Region" and a["metro_area"] == "Mumbai Region"


def test_repost_and_id_dedupe():
    raw = pd.DataFrame([_raw(job_id="1"), _raw(job_id="1"), _raw(job_id="2")])  # 2 = repost
    assert len(clean_and_transform(raw)) == 1


def test_experience_and_entry_level():
    raw = pd.DataFrame([
        _raw(job_id="1", company_name="A", job_description="Requires 2-4 years of experience in SQL."),
        _raw(job_id="2", company_name="B", job_description="Experience: 3+ years. Power BI."),
        _raw(job_id="3", company_name="C", job_description="Freshers welcome. Company founded 25 years ago."),
        _raw(job_id="4", company_name="D", job_description="0-1 years of experience."),
    ])
    out = clean_and_transform(raw).set_index("job_id")
    assert out.loc["1", "min_experience_years"] == 2
    assert out.loc["2", "min_experience_years"] == 3
    assert pd.isna(out.loc["3", "min_experience_years"]) and out.loc["3", "entry_level_friendly"]
    assert out.loc["4", "min_experience_years"] == 0 and out.loc["4", "entry_level_friendly"]


def test_skill_regexes():
    txt = pd.Series(["we use r&d and rd", "python, r and sas", "knowledge of r programming",
                     "statistical analysis", "t-sql and postgresql", "pyspark on aws",
                     "a/b testing", "remote or hybrid"])
    def hit(skill): return txt.str.contains(config.SKILL_PATTERNS[skill], regex=True).tolist()
    assert hit("R") == [False, True, True, False, False, False, False, False]
    assert hit("Statistics")[3] and hit("Statistics")[6]
    assert hit("SQL")[4] and hit("Big_Data")[5] and hit("Cloud")[5]
    assert hit("SAS")[1]


def test_work_mode_priority():
    s = pd.Series(["hybrid or remote role", "fully remote", "work from office", "nothing here"], dtype="string")
    assert cleaner.detect_work_mode(s).tolist() == ["Hybrid", "Remote", "On-site", "Not Specified"]


def test_skills_long_matches_flags():
    out = clean_and_transform(generate_raw_jobs(120))
    long = build_skills_long(out)
    assert len(long) == int(out["skill_count"].sum())
    assert long["job_id"].isin(out["job_id"]).all()


# ---------------- scraper (offline, fake HTTP) ----------------
CARD = ('<div class="base-card" data-entity-urn="urn:li:jobPosting:{id}"><a class="base-card__full-link" '
        'href="https://x/jobs/view/{id}?trk=1"></a><h3 class="base-search-card__title">Data Analyst</h3>'
        '<h4 class="base-search-card__subtitle">Co {id}</h4><span class="job-search-card__location">'
        'Mumbai, Maharashtra, India</span><time datetime="2026-11-05"></time></div>')
DETAIL = ('<div class="show-more-less-html__markup">Need SQL and Python</div>'
          '<li class="description__job-criteria-item"><h3 class="description__job-criteria-subheader">'
          'Seniority level</h3><span class="description__job-criteria-text">Entry level</span></li>')


class FakeResp:
    def __init__(self, text, status=200): self.text, self.status_code = text, status


class FakeSession:
    def __init__(self): self.headers, self.calls = {}, []
    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if "seeMoreJobPostings" in url:
            start = params["start"]
            if start >= 10:
                return FakeResp("")                      # results ended
            return FakeResp("".join(CARD.format(id=1000 + start + i) for i in range(10)))
        return FakeResp(DETAIL)


def test_scraper_offset_filters_and_existing_ids(monkeypatch=None):
    fake = FakeSession()
    scraper.build_session = lambda: fake
    scraper.time.sleep = lambda s: None
    df = scraper.scrape_linkedin_jobs(["Data Analyst"], "Mumbai", 3, existing_ids={"1000", "1001"},
                                      date_posted="week", experience_levels=[1, 2], sort_by="recent")
    searches = [p for u, p in fake.calls if "seeMoreJobPostings" in u]
    assert [p["start"] for p in searches] == [0, 10]      # offset follows cards received, stops when empty
    assert searches[0]["f_TPR"] == "r604800" and searches[0]["f_E"] == "1,2" and searches[0]["sortBy"] == "DD"
    assert len(df) == 8 and not df["job_id"].isin(["1000", "1001"]).any()   # existing ids skipped
    assert df["job_description"].notna().all()
    assert list(df.columns) == scraper.RAW_COLUMNS
