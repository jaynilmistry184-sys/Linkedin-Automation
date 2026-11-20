"""
db_loader.py - Load cleaned data into MySQL (default), SQL Server or SQLite.

Tables (created automatically; schema is built from config.SKILL_PATTERNS):
  fact_linkedin_jobs  one row per job             (PK job_id)
  fact_job_skills     one row per job x skill     (PK job_id + skill) - for Power BI
  pipeline_runs       one row per pipeline run    (audit log)

Views (same names as your notebook, rebuilt by create_views()):
  vw_skill_demand_summary, vw_unpivoted_skills, vw_company_hiring_velocity

Safe to run against your EXISTING database: missing columns are added to the jobs
table, and old rows are back-filled where possible.
"""
import logging
import urllib.parse
from datetime import datetime
from typing import Optional, Tuple

import pandas as pd
from sqlalchemy import (
    Boolean, Column, Date, DateTime, Index, Integer, MetaData, String, Table,
    Unicode, UnicodeText, create_engine, inspect, select, text,
)
from sqlalchemy.dialects import mysql

import config

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------
def _mysql_url(with_database: bool = True) -> str:
    c = config.DB_CONFIG
    user = urllib.parse.quote_plus(str(c["user"]))
    pwd = urllib.parse.quote_plus(str(c["password"]))
    db = c["database"] if with_database else ""
    return (f"mysql+{c['mysql_driver']}://{user}:{pwd}@{c['host']}:{c['port']}/{db}"
            f"?charset=utf8mb4")


def get_db_engine():
    """SQLAlchemy engine for the backend selected in config.DB_BACKEND."""
    backend = config.DB_BACKEND
    if backend == "sqlite":
        return create_engine(f"sqlite:///{config.SQLITE_PATH}")
    if backend == "mysql":
        return create_engine(_mysql_url(), pool_pre_ping=True)
    if backend == "sqlserver":
        c = config.DB_CONFIG
        if str(c["trusted_connection"]).lower() == "yes":
            auth = "Trusted_Connection=yes;"
        else:
            auth = f"UID={c['user']};PWD={c['password']};"
        conn_str = (f"DRIVER={{{c['driver']}}};SERVER={c['server']};DATABASE={c['database']};"
                    f"{auth}TrustServerCertificate=yes;")
        url = f"mssql+pyodbc:///?odbc_connect={urllib.parse.quote_plus(conn_str)}"
        return create_engine(url, fast_executemany=config.FAST_EXECUTEMANY)
    raise ValueError(f"Unknown DB_BACKEND '{backend}' (use mysql, sqlserver or sqlite)")


def check_connection(engine) -> Tuple[bool, str]:
    """Return (ok, message). Never raises - handy for notebooks."""
    try:
        ensure_database()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, f"Connected ({config.DB_BACKEND})."
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def ensure_database() -> None:
    """MySQL: create the database if it does not exist yet (needs CREATE privilege)."""
    if config.DB_BACKEND != "mysql":
        return
    name = config.DB_CONFIG["database"]
    try:
        tmp = create_engine(_mysql_url(with_database=False))
        try:
            with tmp.begin() as conn:
                conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4"))
        finally:
            tmp.dispose()
    except Exception as exc:  # noqa: BLE001 - user may lack CREATE privilege; the real connect will tell
        logger.warning("Could not auto-create database '%s' (%s). Assuming it already exists.", name, exc)


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------
def skill_flag_columns() -> list:
    return [f"has_{s.lower()}" for s in config.SKILL_PATTERNS]


def build_metadata():
    """Return (metadata, jobs_table, skills_table, runs_table)."""
    md = MetaData()
    jt = config.JOBS_TABLE
    long_text = UnicodeText().with_variant(mysql.LONGTEXT(), "mysql")
    opts = {"mysql_charset": "utf8mb4"}

    jobs = Table(
        jt, md,
        Column("job_id", String(50), primary_key=True),
        Column("search_keyword", String(100)),
        Column("search_location", String(100)),
        Column("job_title", Unicode(255)),
        Column("standardized_role", String(100)),
        Column("company_name", Unicode(255)),
        Column("location", Unicode(255)),
        Column("city", Unicode(100)),
        Column("state", Unicode(100)),
        Column("country", Unicode(100)),
        Column("metro_area", Unicode(100)),
        Column("seniority_level", String(100)),
        Column("employment_type", String(100)),
        Column("job_function", Unicode(255)),
        Column("industries", Unicode(255)),
        Column("work_mode", String(30)),
        Column("min_experience_years", Integer),
        Column("entry_level_friendly", Boolean),
        Column("posted_date", Date),
        Column("scraped_at", DateTime),
        Column("job_url", String(500)),
        Column("description_fetched", Boolean),
        Column("extracted_skills", String(500)),
        Column("skill_count", Integer),
        *[Column(c, Boolean) for c in skill_flag_columns()],
        Column("job_description", long_text),
        Index(f"ix_{jt}_posted_date", "posted_date"),
        Index(f"ix_{jt}_role", "standardized_role"),
        Index(f"ix_{jt}_city", "city"),
        **opts,
    )
    # No ForeignKey on purpose: it can clash with the collation of an older table you
    # already created in Workbench. Power BI relates the tables on job_id anyway.
    skills = Table(
        config.SKILLS_TABLE, md,
        Column("job_id", String(50), primary_key=True),
        Column("skill", String(50), primary_key=True),
        **opts,
    )
    runs = Table(
        config.RUNS_TABLE, md,
        Column("run_id", Integer, primary_key=True, autoincrement=True),
        Column("started_at", DateTime),
        Column("finished_at", DateTime),
        Column("status", String(20)),
        Column("rows_scraped", Integer),
        Column("rows_cleaned", Integer),
        Column("rows_inserted", Integer),
        Column("error_message", UnicodeText),
        **opts,
    )
    return md, jobs, skills, runs


def initialize_database(engine) -> None:
    """Create missing tables/indexes, add missing columns, back-fill old rows."""
    ensure_database()
    md, jobs, skills, _ = build_metadata()
    md.create_all(engine, checkfirst=True)

    existing = {c["name"].lower() for c in inspect(engine).get_columns(jobs.name)}
    added = []
    for col in jobs.columns:
        if col.name.lower() not in existing:
            ddl_type = col.type.compile(dialect=engine.dialect)
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {jobs.name} ADD {col.name} {ddl_type} NULL"))
            added.append(col.name)
    if added:
        logger.info("Added missing columns to %s: %s", jobs.name, ", ".join(added))

    with engine.begin() as conn:
        if "description_fetched" in added:   # rows loaded by the old pipeline
            conn.execute(text(
                f"UPDATE {jobs.name} SET description_fetched = "
                f"CASE WHEN job_description IS NOT NULL AND job_description <> '' THEN 1 ELSE 0 END"))
        n_skill_rows = conn.execute(select(skills.c.job_id).limit(1)).first()
        n_jobs = conn.execute(select(jobs.c.job_id).limit(1)).first()
        if n_skill_rows is None and n_jobs is not None:   # build long table from has_* flags
            for col in skill_flag_columns():
                label = {f"has_{s.lower()}": s.replace("_", " ") for s in config.SKILL_PATTERNS}[col]
                if col in existing or col in added:
                    conn.execute(text(
                        f"INSERT INTO {skills.name} (job_id, skill) "
                        f"SELECT job_id, '{label}' FROM {jobs.name} WHERE {col} = 1"))
            logger.info("Back-filled %s from existing has_* flags.", skills.name)
    logger.info("Database ready (%s).", config.DB_BACKEND)


# --------------------------------------------------------------------------
# Views (portable SQL: no backticks, no BIT aggregates)
# --------------------------------------------------------------------------
def _view_definitions() -> dict:
    jt, st = config.JOBS_TABLE, config.SKILLS_TABLE
    parts = []
    for col in skill_flag_columns():
        base = col[4:]
        parts.append(f"SUM(CASE WHEN description_fetched = 1 THEN {col} * 1 ELSE 0 END) AS {base}_count")
        parts.append(f"ROUND(AVG(CASE WHEN description_fetched = 1 THEN {col} * 1.0 END) * 100, 1) AS {base}_pct")
    summary_cols = ",\n        ".join(parts)

    return {
        "vw_skill_demand_summary": f"""
    SELECT standardized_role,
        COUNT(job_id) AS total_postings,
        SUM(CASE WHEN description_fetched = 1 THEN 1 ELSE 0 END) AS postings_with_description,
        {summary_cols}
    FROM {jt}
    GROUP BY standardized_role""",
        "vw_unpivoted_skills": f"""
    SELECT s.job_id, j.standardized_role, j.company_name, j.seniority_level, j.city,
           j.metro_area, j.posted_date, s.skill AS Skill
    FROM {st} s
    JOIN {jt} j ON j.job_id = s.job_id""",
        "vw_company_hiring_velocity": f"""
    SELECT company_name, standardized_role, city,
        COUNT(job_id) AS active_openings,
        ROUND(AVG(skill_count * 1.0), 1) AS avg_skills_requested,
        MAX(posted_date) AS latest_posting_date
    FROM {jt}
    GROUP BY company_name, standardized_role, city""",
    }


def create_views(engine) -> None:
    """(Re)create the analytical views for Power BI / Tableau / Workbench."""
    dialect = engine.dialect.name
    with engine.begin() as conn:
        for name, select_sql in _view_definitions().items():
            if dialect == "mysql":
                conn.execute(text(f"CREATE OR REPLACE VIEW {name} AS {select_sql}"))
            elif dialect == "mssql":
                conn.execute(text(f"CREATE OR ALTER VIEW {name} AS {select_sql}"))
            else:  # sqlite
                conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
                conn.execute(text(f"CREATE VIEW {name} AS {select_sql}"))
    logger.info("Analytical views refreshed.")


# --------------------------------------------------------------------------
# Read helpers
# --------------------------------------------------------------------------
def get_existing_job_ids(engine) -> set:
    _, jobs, _, _ = build_metadata()
    with engine.connect() as conn:
        return {str(r[0]) for r in conn.execute(select(jobs.c.job_id)).fetchall()}


def read_query(sql: str, engine=None) -> pd.DataFrame:
    engine = engine or get_db_engine()
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn)


def read_jobs(engine=None) -> pd.DataFrame:
    return read_query(f"SELECT * FROM {config.JOBS_TABLE}", engine)


def read_skills(engine=None) -> pd.DataFrame:
    return read_query(f"SELECT * FROM {config.SKILLS_TABLE}", engine)


# --------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------
def _to_db_values(frame: pd.DataFrame) -> pd.DataFrame:
    """Plain Python objects, with every missing value (NaN / NaT / pd.NA) as None -> SQL NULL."""
    return frame.astype(object).where(frame.notna(), None)


def load_to_database(df: pd.DataFrame, skills_df: pd.DataFrame, engine=None) -> int:
    """Insert only NEW jobs (plus their skills) in one transaction. Returns jobs inserted."""
    if df is None or df.empty:
        logger.info("No data to load.")
        return 0

    engine = engine or get_db_engine()
    initialize_database(engine)
    _, jobs, skills, _ = build_metadata()

    existing = get_existing_job_ids(engine)
    new_df = df[~df["job_id"].astype(str).isin(existing)].copy()
    if new_df.empty:
        logger.info("All jobs already exist in the database. 0 rows inserted.")
        return 0

    job_cols = [c.name for c in jobs.columns if c.name in new_df.columns]
    new_df = _to_db_values(new_df[job_cols])
    new_skills = skills_df[skills_df["job_id"].astype(str).isin(set(new_df["job_id"].astype(str)))]

    with engine.begin() as conn:   # atomic: jobs + skills together
        new_df.to_sql(jobs.name, conn, if_exists="append", index=False, chunksize=100)
        if not new_skills.empty:
            new_skills[["job_id", "skill"]].to_sql(skills.name, conn, if_exists="append",
                                                   index=False, chunksize=500)
    logger.info("Inserted %d new jobs and %d skill rows.", len(new_df), len(new_skills))
    return len(new_df)


# --------------------------------------------------------------------------
# Run log
# --------------------------------------------------------------------------
def log_run_start(engine) -> int:
    _, _, _, runs = build_metadata()
    with engine.begin() as conn:
        res = conn.execute(runs.insert().values(started_at=datetime.now(), status="RUNNING"))
        return int(res.inserted_primary_key[0])


def log_run_finish(engine, run_id: int, status: str, rows_scraped: int = 0,
                   rows_cleaned: int = 0, rows_inserted: int = 0,
                   error_message: Optional[str] = None) -> None:
    _, _, _, runs = build_metadata()
    with engine.begin() as conn:
        conn.execute(runs.update().where(runs.c.run_id == run_id).values(
            finished_at=datetime.now(), status=status, rows_scraped=rows_scraped,
            rows_cleaned=rows_cleaned, rows_inserted=rows_inserted,
            error_message=(error_message or "")[:4000] or None))
