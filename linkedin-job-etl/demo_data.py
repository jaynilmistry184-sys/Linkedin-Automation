"""
demo_data.py - SYNTHETIC job postings in the same shape as the scraper output.

Used for tests and for running the notebook before you have scraped anything.
The data is randomly generated: do NOT draw real conclusions from it.
Every job_id starts with "demo-".
"""
import numpy as np
import pandas as pd

ROLES = {
    "Data Analyst": (["Data Analyst", "Junior Data Analyst", "Senior Data Analyst", "Data Analyst - Reporting"],
                     {"sql": .85, "excel": .7, "power bi": .5, "python": .45, "tableau": .2, "statistics": .25}),
    "Business Analyst": (["Business Analyst", "Associate Business Analyst", "Senior Business Analyst"],
                         {"sql": .6, "excel": .75, "power bi": .35, "python": .15, "statistics": .1}),
    "BI Developer": (["Power BI Developer", "BI Developer", "Tableau Developer", "Business Intelligence Analyst"],
                     {"power bi": .8, "sql": .8, "excel": .4, "tableau": .35, "etl": .25, "dax": .5}),
    "Data Scientist": (["Data Scientist", "Machine Learning Engineer"],
                       {"python": .95, "machine learning": .9, "sql": .6, "statistics": .6, "aws": .3}),
    "Data Engineer": (["Data Engineer", "Analytics Engineer"],
                      {"python": .85, "sql": .9, "etl": .7, "airflow": .35, "aws": .45, "pyspark": .3}),
}
CITIES = [("Mumbai", "Maharashtra"), ("Navi Mumbai", "Maharashtra"), ("Pune", "Maharashtra"),
          ("Bengaluru", "Karnataka")]
COMPANIES = [f"Demo Company {i:02d}" for i in range(1, 41)]


def generate_raw_jobs(n: int = 300, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    role_names = list(ROLES)
    role_p = [.35, .25, .15, .1, .15]
    today = pd.Timestamp.today().normalize()
    rows = []
    for i in range(n):
        role = role_names[rng.choice(len(role_names), p=role_p)]
        titles, skills = ROLES[role]
        title = str(rng.choice(titles))
        city, state = CITIES[rng.choice(len(CITIES), p=[.55, .15, .15, .15])]
        years = int(rng.choice([0, 1, 2, 3, 5], p=[.15, .25, .3, .2, .1]))
        wanted = [k for k, p in skills.items() if rng.random() < p]
        mode = str(rng.choice(["hybrid", "work from office", "remote", ""], p=[.3, .4, .1, .2]))
        desc = (f"We are hiring a {title}. Requirements: {', '.join(wanted)}. "
                f"{years} years of relevant experience. {mode}. "
                f"{'Freshers can apply. ' if years == 0 else ''}Prepare weekly reports.")
        rows.append({
            "job_id": f"demo-{100000 + i}",
            "search_keyword": role,
            "search_location": "Mumbai",
            "job_title": title,
            "company_name": str(rng.choice(COMPANIES)),
            "location": f"{city}, {state}, India",
            "posted_date": (today - pd.Timedelta(days=int(rng.integers(0, 28)))).strftime("%Y-%m-%d"),
            "job_url": None,
            "job_description": None if rng.random() < .1 else desc,   # ~10% failed detail fetches
            "seniority_level": str(rng.choice(["Entry level", "Associate", "Mid-Senior level", None],
                                              p=[.3, .3, .3, .1])),
            "employment_type": str(rng.choice(["Full-time", "Contract", None], p=[.85, .08, .07])),
            "job_function": "Analyst",
            "industries": str(rng.choice(["IT Services", "Financial Services", "Retail", "Healthcare"])),
        })
    return pd.DataFrame(rows)
