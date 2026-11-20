"""
config.py - central settings for the LinkedIn Job Market pipeline.

Secrets (DB passwords etc.) are read from environment variables or a local
.env file (see .env.example). Never hard-code passwords in this file.
"""
import os
from pathlib import Path

try:  # optional: pip install python-dotenv
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent
BACKUP_DIR = BASE_DIR / "data_backups"      # raw + cleaned CSV snapshots
EXPORT_DIR = BASE_DIR / "powerbi_exports"   # CSVs for Power BI (written by notebook)

# --------------------------------------------------------------------------
# SCRAPER SETTINGS
# --------------------------------------------------------------------------
SEARCH_KEYWORDS = [
    "Data Analyst",
    "Business Analyst",
    "BI Developer",
    "Power BI Developer",
    "Business Intelligence Analyst",
    "Reporting Analyst",
    "MIS Executive",
    "SQL Developer",
    "Data Analyst Fresher",
]

# Start with one city. Add "Navi Mumbai", "Pune", "Bengaluru" to compare cities
# (each extra location multiplies the number of requests, so scrape time too).
SEARCH_LOCATIONS = ["Mumbai"]

# The guest endpoint returns roughly 10-25 cards per request. The scraper
# advances the offset by the number of cards actually received.
PAGES_PER_KEYWORD = 2

# LinkedIn guest-search filters (set to None to disable a filter)
DATE_POSTED = "week"        # "24h" | "week" | "month" | None
EXPERIENCE_LEVELS = None    # e.g. [1, 2] -> 1 Internship, 2 Entry level, 3 Associate,
                            # 4 Mid-Senior, 5 Director, 6 Executive. Verify in your browser's
                            # network tab, LinkedIn can change these codes.
SORT_BY = "recent"          # "recent" | "relevant"

DATE_POSTED_CODES = {"24h": "r86400", "week": "r604800", "month": "r2592000"}
SORT_CODES = {"recent": "DD", "relevant": "R"}

# Politeness delays (seconds)
DETAIL_DELAY = (1.2, 2.8)   # before each job-detail request
PAGE_DELAY = (2.5, 5.0)     # between search pages
REQUEST_TIMEOUT = 12
MAX_RETRIES = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# --------------------------------------------------------------------------
# SKILL EXTRACTION (regex, matched against lower-cased title + description)
# Rules: word boundaries (\b) to avoid false positives, NO capturing groups.
# The key becomes the column has_<key lower-cased> and the label key.replace("_", " ").
# --------------------------------------------------------------------------
SKILL_PATTERNS = {
    "Python": r"\bpython\b|\bpandas\b|\bnumpy\b",
    "R": r"\br\s+(?:programming|language|studio)\b|\brstudio\b|\bpython\s*(?:,|/|and|or)\s*r\b(?![&\w.])",
    "SQL": r"\bsql\b|\bmysql\b|\bpostgres\w*\b|\btsql\b|\bt-sql\b|\bplsql\b|\bpl/sql\b",
    "Power_BI": r"\bpower\s*bi\b|\bdax\b|\bpower\s*query\b",
    "Tableau": r"\btableau\b",
    "Looker": r"\blooker\b|\blooker\s*studio\b|\bdata\s*studio\b",
    "Excel": r"\bexcel\b|\bvlookup\b|\bxlookup\b|\bpivot\s*tables?\b|\bvba\b",
    "Google_Sheets": r"\bgoogle\s*sheets?\b|\bgsheets?\b|\bapps\s*script\b",
    "SAS": r"\bsas\b",
    "ETL": r"\betl\b|\belt\b|\bdata\s*pipelines?\b|\bssis\b|\binformatica\b",
    "Airflow_dbt": r"\bairflow\b|\bdbt\b",
    "Big_Data": r"\bpyspark\b|\bapache\s*spark\b|\bspark\s*sql\b|\bhadoop\b|\bhive\b",
    "Cloud": r"\baws\b|\bazure\b|\bgcp\b|\bsnowflake\b|\bbigquery\b|\bdatabricks\b|\bredshift\b",
    "Git": r"\bgit\b|\bgithub\b|\bgitlab\b",
    "Statistics": r"\bstatistic\w*\b|\bhypothesis\s*testing\b|\ba/b\s*test\w*\b|\bregression\b",
    "Machine_Learning": r"\bmachine\s*learning\b|\bml\b|\bscikit-?learn\b|\bsklearn\b|\bdeep\s*learning\b|\bnlp\b",
    "Data_Viz": r"\bdata\s*visuali[sz]ation\b|\bmatplotlib\b|\bseaborn\b|\bplotly\b",
}

# Work-mode detection (checked in this order; first match wins)
WORK_MODE_PATTERNS = {
    "Hybrid": r"\bhybrid\b",
    "Remote": r"\bremote\b|\bwork\s*from\s*home\b|\bwfh\b",
    "On-site": r"\bon-?\s?site\b|\bwork\s*from\s*office\b|\bwfo\b|\bin-?office\b",
}

# LinkedIn reports places like "Mumbai Metropolitan Region" or "Navi Mumbai".
# metro_area groups them so Mumbai-region jobs are not split across several "cities".
METRO_AREAS = {
    "Mumbai Region": ["mumbai", "navi mumbai", "thane", "mumbai metropolitan region",
                      "greater mumbai", "panvel", "kalyan", "dombivli", "vasai", "mira bhayandar"],
    "Pune Region": ["pune", "pimpri-chinchwad", "pune metropolitan region"],
}

# City names are normalised with this map (lower-case key -> canonical name)
CITY_ALIASES = {
    "bangalore": "Bengaluru",
    "bombay": "Mumbai",
    "gurgaon": "Gurugram",
    "new delhi": "Delhi",
    "madras": "Chennai",
    "calcutta": "Kolkata",
}

# --------------------------------------------------------------------------
# DATABASE
# DB_BACKEND = "mysql"      (MySQL Workbench / MySQL Server - default)
#            = "sqlserver"  (SQL Server + ODBC driver)
#            = "sqlite"     (zero-setup file: job_market.db)
# Put real credentials in a .env file (see .env.example), never in this file.
# --------------------------------------------------------------------------
DB_BACKEND = os.getenv("DB_BACKEND", "mysql").lower()

DB_CONFIG = {
    # MySQL (and user/password for SQL Server when DB_TRUSTED=no)
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "JobMarketAnalytics"),
    "mysql_driver": os.getenv("MYSQL_DRIVER", "mysqlconnector"),   # or "pymysql"
    # SQL Server only
    "server": os.getenv("DB_SERVER", "localhost\\SQLEXPRESS"),
    "driver": os.getenv("DB_DRIVER", "ODBC Driver 17 for SQL Server"),
    "trusted_connection": os.getenv("DB_TRUSTED", "yes"),
}
SQLITE_PATH = Path(os.getenv("SQLITE_PATH", BASE_DIR / "job_market.db"))

JOBS_TABLE = "fact_linkedin_jobs"
SKILLS_TABLE = "fact_job_skills"
RUNS_TABLE = "pipeline_runs"

# SQL Server only: fast but can fail / use lots of memory with NVARCHAR(MAX) columns.
FAST_EXECUTEMANY = False
