"""
main.py - Orchestrates Extract -> Transform -> Load.

Examples
  python main.py                                   # defaults from config.py
  python main.py --pages 1 --keywords "Data Analyst" "SQL Developer"
  python main.py --locations Mumbai Pune --date-posted 24h
  python main.py --from-raw data_backups/raw_20261118_101500.csv   # re-clean without scraping
  python main.py --no-db                            # scrape + clean + CSV only
"""
import argparse
import logging
from datetime import datetime
from typing import Optional

import pandas as pd

import config
from scraper import scrape_linkedin_jobs
from cleaner import clean_and_transform, build_skills_long
import db_loader

logger = logging.getLogger("ETL_Main")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=[logging.FileHandler(config.BASE_DIR / "pipeline.log", encoding="utf-8"),
                  logging.StreamHandler()],
        force=True,
    )


def run_pipeline(keywords=None, locations=None, pages: Optional[int] = None,
                 date_posted=config.DATE_POSTED, from_raw: Optional[str] = None,
                 load_db: bool = True) -> dict:
    """Run the full pipeline. Returns a dict with the cleaned DataFrame and run statistics."""
    keywords = keywords or config.SEARCH_KEYWORDS
    locations = locations or config.SEARCH_LOCATIONS
    pages = pages or config.PAGES_PER_KEYWORD
    stats = {"rows_scraped": 0, "rows_cleaned": 0, "rows_inserted": 0, "cleaned_df": pd.DataFrame()}

    logger.info("=== Starting LinkedIn Job Market ETL Pipeline ===")
    engine = run_id = None
    existing_ids: set = set()

    if load_db:  # fail fast: check the database BEFORE spending minutes scraping
        engine = db_loader.get_db_engine()
        db_loader.initialize_database(engine)
        run_id = db_loader.log_run_start(engine)
        existing_ids = db_loader.get_existing_job_ids(engine)
        logger.info("%d jobs already in the database (will be skipped when scraping).", len(existing_ids))

    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        config.BACKUP_DIR.mkdir(exist_ok=True)

        # 1. Extract
        if from_raw:
            raw_df = pd.read_csv(from_raw, encoding="utf-8-sig", dtype={"job_id": str})
            logger.info("Loaded %d raw rows from %s", len(raw_df), from_raw)
        else:
            raw_df = scrape_linkedin_jobs(keywords, locations, pages, existing_ids=existing_ids,
                                          date_posted=date_posted)
            if not raw_df.empty:
                raw_df.to_csv(config.BACKUP_DIR / f"raw_{ts}.csv", index=False, encoding="utf-8-sig")
        stats["rows_scraped"] = len(raw_df)

        if raw_df.empty:
            logger.warning("Extraction returned 0 new records. Nothing to do.")
            if engine is not None:
                db_loader.log_run_finish(engine, run_id, "NO_NEW_DATA")
            return stats

        # 2. Transform
        cleaned = clean_and_transform(raw_df)
        skills_long = build_skills_long(cleaned)
        cleaned.to_csv(config.BACKUP_DIR / f"cleaned_{ts}.csv", index=False, encoding="utf-8-sig")
        stats.update(rows_cleaned=len(cleaned), cleaned_df=cleaned)

        # 3. Load
        if engine is not None:
            stats["rows_inserted"] = db_loader.load_to_database(cleaned, skills_long, engine)
            db_loader.create_views(engine)
            db_loader.log_run_finish(engine, run_id, "SUCCESS", stats["rows_scraped"],
                                     stats["rows_cleaned"], stats["rows_inserted"])
        logger.info("=== Pipeline completed: scraped=%d cleaned=%d inserted=%d ===",
                    stats["rows_scraped"], stats["rows_cleaned"], stats["rows_inserted"])
        return stats

    except Exception as exc:
        logger.exception("Pipeline failed: %s", exc)
        if engine is not None and run_id is not None:
            try:
                db_loader.log_run_finish(engine, run_id, "FAILED", stats["rows_scraped"],
                                         stats["rows_cleaned"], stats["rows_inserted"], str(exc))
            except Exception:
                logger.error("Could not write the failure to pipeline_runs.")
        raise


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="LinkedIn job market ETL pipeline")
    p.add_argument("--keywords", nargs="+", help="override SEARCH_KEYWORDS")
    p.add_argument("--locations", nargs="+", help="override SEARCH_LOCATIONS")
    p.add_argument("--pages", type=int, help="pages per keyword")
    p.add_argument("--date-posted", choices=["24h", "week", "month", "any"], default=None)
    p.add_argument("--from-raw", help="skip scraping, clean this raw CSV instead")
    p.add_argument("--no-db", action="store_true", help="do not load into the database")
    return p.parse_args(argv)


if __name__ == "__main__":
    setup_logging()
    a = parse_args()
    dp = config.DATE_POSTED if a.date_posted is None else (None if a.date_posted == "any" else a.date_posted)
    run_pipeline(a.keywords, a.locations, a.pages, dp, a.from_raw, load_db=not a.no_db)
