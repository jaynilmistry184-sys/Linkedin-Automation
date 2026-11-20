"""
cleaner.py - Transform raw scraped jobs into an analysis-ready DataFrame.

Principle: never invent data. Missing dates stay NULL, missing categories become
"Not Specified", and jobs whose description could not be fetched are flagged with
description_fetched = False so they can be excluded from skill-demand statistics.
"""
import re
import logging
from datetime import datetime

import numpy as np
import pandas as pd

import config
from config import SKILL_PATTERNS

logger = logging.getLogger(__name__)

SKILL_COLS = [f"has_{s.lower()}" for s in SKILL_PATTERNS]
SKILL_LABELS = {f"has_{s.lower()}": s.replace("_", " ") for s in SKILL_PATTERNS}

TEXT_COLS = ["job_title", "company_name", "location", "seniority_level",
             "employment_type", "job_function", "industries", "search_keyword",
             "search_location", "job_url"]
RAW_COLS = TEXT_COLS + ["job_id", "posted_date", "job_description"]

# --------------------------------------------------------------------------
# Role standardisation (regex with word boundaries; first matching rule wins)
# --------------------------------------------------------------------------
ROLE_RULES = [
    ("Data / Analytics Engineer", r"data engineer|\betl\b|analytics engineer|data pipeline"),
    ("BI Developer", r"power\s*bi|tableau|\bbi\b|business intelligence|looker"),
    ("Business Analyst", r"business analyst"),
    ("Data Scientist", r"data scientist|machine learning|\bai\b|\bml\b|\bnlp\b|deep learning"),
    ("Data Analyst", r"data analyst|product analyst|marketing analyst|financial analyst|"
                     r"reporting analyst|insights? analyst|\bmis\b|analytics"),
]
_ROLE_RULES_C = [(name, re.compile(p, re.IGNORECASE)) for name, p in ROLE_RULES]


def standardize_role(title) -> str:
    """Map a noisy job title to a role family."""
    if not isinstance(title, str):
        return "Other"
    for name, pattern in _ROLE_RULES_C:
        if pattern.search(title):
            return name
    return "Other Analytical Role"


# --------------------------------------------------------------------------
# Location parsing: "Mumbai, Maharashtra, India" -> (Mumbai, Maharashtra, India)
# --------------------------------------------------------------------------
def parse_location(loc):
    if not isinstance(loc, str) or not loc.strip():
        return ("Unknown", "Unknown", "Unknown")
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    if not parts:
        return ("Unknown", "Unknown", "Unknown")
    city, state, country = parts[0], "Unknown", "Unknown"
    if len(parts) == 1:
        if city.lower() == "india":
            return ("Unknown", "Unknown", "India")
    elif len(parts) == 2:
        if parts[1].lower() == "india":
            country = "India"
        else:
            state = parts[1]
    else:
        state, country = parts[1], parts[-1]
    city = config.CITY_ALIASES.get(city.lower(), city)
    return (city, state, country)


# --------------------------------------------------------------------------
# Description-derived features
# --------------------------------------------------------------------------
_EXP_A = re.compile(r"(\d{1,2})(?:\s*(?:-|–|—|to)\s*\d{1,2})?\s*\+?\s*(?:years?|yrs?)\b[^.]{0,50}?\bexperience",
                    re.IGNORECASE)
_EXP_B = re.compile(r"\bexperience[^.\d]{0,30}?(\d{1,2})(?:\s*(?:-|–|—|to)\s*\d{1,2})?\s*\+?\s*(?:years?|yrs?)\b",
                    re.IGNORECASE)


def extract_min_experience(text: pd.Series) -> pd.Series:
    """Lowest number of years mentioned next to the word 'experience' (nullable Int64)."""
    a = text.str.extract(_EXP_A)[0]
    b = text.str.extract(_EXP_B)[0]
    years = pd.to_numeric(a.fillna(b), errors="coerce")
    years = years.where(years <= 20)  # "25 years of company history" is not a requirement
    return years.astype("Int64")


def detect_work_mode(text: pd.Series) -> pd.Series:
    mode = pd.Series("Not Specified", index=text.index, dtype="object")
    for label, pattern in reversed(list(config.WORK_MODE_PATTERNS.items())):
        mode = mode.mask(text.str.contains(pattern, regex=True), label)  # first rule wins
    return mode


def _clean_text(s: pd.Series) -> pd.Series:
    s = s.astype("string").str.replace(r"\s+", " ", regex=True).str.strip()
    return s.mask(s.isin(["", "None", "nan", "NaN"]))


# --------------------------------------------------------------------------
# Main transform
# --------------------------------------------------------------------------
def clean_and_transform(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        logger.warning("Received empty DataFrame for cleaning.")
        return pd.DataFrame()

    initial = len(df)
    df = df.dropna(how="all").copy()
    for col in RAW_COLS:
        if col not in df.columns:
            df[col] = None

    # 1. Types, de-duplicate on job_id, drop rows missing critical fields
    for col in TEXT_COLS + ["job_description"]:
        df[col] = _clean_text(df[col])
    df["job_id"] = df["job_id"].astype("string").str.strip()
    df = df.dropna(subset=["job_id", "job_title", "company_name"])
    df = df.drop_duplicates(subset=["job_id"], keep="first")

    # 2. Description availability flag (do this BEFORE any filling)
    df["description_fetched"] = df["job_description"].notna()

    # 3. Categorical defaults (no invented "Full-time")
    for col in ["seniority_level", "employment_type", "job_function", "industries"]:
        df[col] = df[col].fillna("Not Specified")

    df["seniority_level"] = df["seniority_level"].replace({"Not Applicable": "Not Specified"})

    # 4. Role family
    df["standardized_role"] = df["job_title"].apply(standardize_role)

    # 5. Location -> city / state / country
    parsed = df["location"].apply(parse_location)
    df[["city", "state", "country"]] = pd.DataFrame(parsed.tolist(), index=df.index)
    metro_lookup = {c: metro for metro, cities in config.METRO_AREAS.items() for c in cities}
    df["metro_area"] = df["city"].str.lower().map(metro_lookup).fillna(df["city"])

    # 6. Dates: leave missing dates NULL
    dates = pd.to_datetime(df["posted_date"], errors="coerce")
    df["posted_date"] = dates.dt.date
    df["scraped_at"] = datetime.now()

    # 7. Reposts: same title + company + city under a new job_id (keep the row with a description)
    key = (df["job_title"].str.lower() + "|" + df["company_name"].str.lower() + "|" + df["city"].str.lower())
    before = len(df)
    df = (df.assign(_k=key)
            .sort_values("description_fetched", ascending=False, kind="stable")
            .drop_duplicates(subset="_k", keep="first")
            .drop(columns="_k")
            .sort_index())
    if before != len(df):
        logger.info("Dropped %d repost duplicates (same title/company/city).", before - len(df))

    # 8. Text features: skills, work mode, experience, entry-level
    desc = df["job_description"].fillna("")
    text = (df["job_title"].fillna("") + " " + desc).str.lower()

    for skill, pattern in SKILL_PATTERNS.items():
        df[f"has_{skill.lower()}"] = text.str.contains(pattern, regex=True).fillna(False).astype(bool)

    flags = df[SKILL_COLS].to_numpy()
    names = list(SKILL_LABELS.values())
    df["extracted_skills"] = [", ".join(n for n, f in zip(names, row) if f) or "None Detected" for row in flags]
    df.loc[~df["description_fetched"], "extracted_skills"] = "Description Unavailable"
    df["skill_count"] = flags.sum(axis=1).astype(int)

    df["work_mode"] = detect_work_mode(text)
    df["min_experience_years"] = extract_min_experience(text)

    fresher = text.str.contains(
        r"\bfreshers?\b|\bentry[\s-]*level\b|\btrainee\b|\bintern(?:ship)?\b|\b(?:recent|fresh)\s+graduates?\b",
        regex=True).fillna(False).astype(bool)
    low_exp = (df["min_experience_years"] <= 1).fillna(False).astype(bool)
    df["entry_level_friendly"] = (fresher | low_exp |
                                  df["seniority_level"].isin(["Entry level", "Internship"])).astype(bool)

    df = df.reset_index(drop=True)
    logger.info("Cleaned: %d records kept from %d raw (%d with descriptions).",
                len(df), initial, int(df["description_fetched"].sum()))
    return df


def build_skills_long(df: pd.DataFrame) -> pd.DataFrame:
    """Long table (job_id, skill) - one row per skill found. Ideal for Power BI visuals."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["job_id", "skill"])
    long = df.melt(id_vars="job_id", value_vars=SKILL_COLS, var_name="skill", value_name="present")
    long = long[long["present"].astype(bool)].copy()
    long["skill"] = long["skill"].map(SKILL_LABELS)
    return long[["job_id", "skill"]].drop_duplicates().reset_index(drop=True)
