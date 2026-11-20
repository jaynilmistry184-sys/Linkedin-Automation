# LinkedIn Job Market ETL + Analytics

Scrape public LinkedIn job postings, clean them, load them into MySQL (or SQL Server / SQLite),
and analyse skill demand for Data / Business Analyst roles. Built for the Mumbai market, configurable for any city.

```
scraper.py  ->  cleaner.py  ->  db_loader.py  ->  analysis_utils.py / notebook  ->  Power BI
 Extract         Transform        Load              Analyse                          Visualise
```

## Setup
```bash
pip install -r requirements.txt          # Python 3.9+
cp .env.example .env                     # then put your MySQL password in .env
```
Start MySQL, then run either:
```bash
jupyter notebook linkedin_job_etl_pipeline.ipynb      # step-by-step, with charts
python main.py --pages 1                              # same pipeline, command line
```
No database yet? Set `DB_BACKEND=sqlite` in `.env`. No scraped data yet? The notebook falls back to
clearly-labelled **synthetic** demo data so you can see every chart.

## What is stored
| Object | Purpose |
|---|---|
| `fact_linkedin_jobs` | one row per job (+ `has_<skill>` flags, `work_mode`, `min_experience_years`, `entry_level_friendly`, `description_fetched`, `metro_area`) |
| `fact_job_skills` | one row per job x skill: use this for Power BI skill visuals |
| `pipeline_runs` | audit log of every run (status, rows scraped/cleaned/inserted, error) |
| `vw_skill_demand_summary`, `vw_unpivoted_skills`, `vw_company_hiring_velocity` | ready-made views for Power BI / Workbench |

An existing `fact_linkedin_jobs` table is upgraded in place (missing columns added, old rows back-filled).

## Design rules
* **Never invent data**: missing dates stay NULL; missing categories are "Not Specified".
* Jobs whose description could not be fetched (usually HTTP 429) get `description_fetched = False` and are
  excluded from skill percentages.
* Already-stored jobs are skipped *before* their detail page is requested.
* Skill regexes are word-bounded and unit-tested (`pytest -q`).

## Add a skill
Add one line to `SKILL_PATTERNS` in `config.py`. The column, table schema, views, long table and notebook
charts all follow automatically (the column is added to your existing table on the next run).

## Limits
* LinkedIn's terms prohibit automated scraping and the guest endpoints rate-limit hard. Keep volumes small, don't publish scraped data.
* Experience, work mode and entry-level flags come from regexes on free text: spot-check them.
* Reposts across different runs are only caught by `job_id`, not by title/company.
