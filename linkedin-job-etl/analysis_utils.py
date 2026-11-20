"""
analysis_utils.py - Data access + reusable analysis helpers for the notebook.
"""
import logging
from typing import Optional

import pandas as pd

import config
import db_loader
from cleaner import SKILL_COLS, SKILL_LABELS, clean_and_transform, build_skills_long

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in SKILL_COLS + ["description_fetched", "entry_level_friendly"]:
        if c in df.columns:
            df[c] = df[c].fillna(False).astype(bool)
    if "posted_date" in df.columns:
        df["posted_date"] = pd.to_datetime(df["posted_date"], errors="coerce")
    if "scraped_at" in df.columns:
        df["scraped_at"] = pd.to_datetime(df["scraped_at"], errors="coerce")
    if "min_experience_years" in df.columns:
        df["min_experience_years"] = pd.to_numeric(df["min_experience_years"], errors="coerce")
    return df


def _latest_cleaned_csv() -> Optional[str]:
    files = sorted(config.BACKUP_DIR.glob("cleaned_*.csv"))
    return str(files[-1]) if files else None


def load_data(source: str = "auto"):
    """
    source: "db" | "csv" | "demo" | "auto" (db -> latest cleaned CSV -> synthetic demo)
    Returns (jobs_df, skills_df, source_used).
    """
    order = ["db", "csv", "demo"] if source == "auto" else [source]
    for src in order:
        try:
            if src == "db":
                engine = db_loader.get_db_engine()
                jobs = db_loader.read_jobs(engine)
                if jobs.empty:
                    raise ValueError("jobs table is empty")
                return _normalise(jobs), db_loader.read_skills(engine), "database"
            if src == "csv":
                path = _latest_cleaned_csv()
                if not path:
                    raise FileNotFoundError("no cleaned_*.csv in data_backups/")
                jobs = _normalise(pd.read_csv(path, dtype={"job_id": str}))
                return jobs, build_skills_long(jobs), f"csv ({path})"
            if src == "demo":
                from demo_data import generate_raw_jobs
                jobs = _normalise(clean_and_transform(generate_raw_jobs()))
                return jobs, build_skills_long(jobs), "SYNTHETIC demo data"
        except Exception as exc:
            logger.warning("Source '%s' not available: %s", src, exc)
    raise RuntimeError(f"Could not load data from: {order}")


# --------------------------------------------------------------------------
# Skill analytics (always on rows whose description was actually fetched)
# --------------------------------------------------------------------------
def with_descriptions(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["description_fetched"]]


def skill_demand(df: pd.DataFrame, by: Optional[str] = None) -> pd.DataFrame:
    """% of postings mentioning each skill. Overall (long) or by a column (wide matrix)."""
    base = with_descriptions(df)
    if by is None:
        out = (base[SKILL_COLS].mean() * 100).rename(index=SKILL_LABELS)
        out = out.sort_values(ascending=False).round(1).reset_index()
        out.columns = ["skill", "pct_of_jobs"]
        out["jobs"] = (base[SKILL_COLS].sum().rename(index=SKILL_LABELS)[out["skill"]].to_numpy())
        return out
    return (base.groupby(by)[SKILL_COLS].mean().mul(100).round(1).rename(columns=SKILL_LABELS))


def skill_cooccurrence(df: pd.DataFrame):
    """Returns (counts, conditional_pct). conditional_pct[row][col] = % of row-skill jobs that also want col."""
    m = with_descriptions(df)[SKILL_COLS].astype(int)
    m.columns = [SKILL_LABELS[c] for c in m.columns]
    counts = m.T.dot(m)
    diag = pd.Series({c: float(counts.loc[c, c]) for c in counts.columns}).replace(0.0, float("nan"))
    cond = counts.div(diag, axis=0).mul(100).round(1)
    return counts, cond


def skill_match(df: pd.DataFrame, my_skills):
    """
    How much of the market do your skills cover, and what should you learn next?
    my_skills: config-style keys, e.g. ["Excel", "SQL", "Python", "Power_BI"].
    Considers postings with a fetched description and at least one detected skill.
    """
    base = with_descriptions(df)
    base = base[base["skill_count"] > 0]
    mine = [f"has_{s.lower()}" for s in my_skills]
    need = base[SKILL_COLS].astype(bool)
    missing = need.copy()
    missing[mine] = False
    n_missing = missing.sum(axis=1)

    summary = {
        "postings_considered": len(base),
        "pct_with_at_least_one_of_my_skills": round(float(need[mine].any(axis=1).mean() * 100), 1),
        "pct_fully_covered": round(float((n_missing == 0).mean() * 100), 1),
    }
    rows = []
    for col in SKILL_COLS:
        if col in mine:
            continue
        rows.append({
            "skill": SKILL_LABELS[col],
            "pct_of_jobs_asking": round(float(need[col].mean() * 100), 1),
            "extra_fully_covered_pct_if_learned": round(float(((n_missing == 1) & missing[col]).mean() * 100), 1),
        })
    gains = (pd.DataFrame(rows).sort_values("extra_fully_covered_pct_if_learned", ascending=False)
             .reset_index(drop=True))
    return summary, gains
