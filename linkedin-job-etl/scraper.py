"""
scraper.py - Extract job postings from LinkedIn's public "guest" endpoints.

NOTE: LinkedIn's terms prohibit automated scraping and the guest endpoints may
rate-limit or block your IP. Keep volumes small, keep the delays, use it for
personal learning only and do not publish scraped data.
"""
import time
import random
import logging
from typing import Iterable, Optional, Union

import requests
from bs4 import BeautifulSoup
import pandas as pd

import config

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

RAW_COLUMNS = [
    "job_id", "search_keyword", "search_location", "job_title", "company_name",
    "location", "posted_date", "job_url", "job_description", "seniority_level",
    "employment_type", "job_function", "industries",
]


_delay_factor = 1.0   # grows after HTTP 429, shrinks again after successes


def _sleep(delay_range) -> None:
    time.sleep(random.uniform(*delay_range) * _delay_factor)


def build_session() -> requests.Session:
    """One Session = connection reuse + shared headers."""
    session = requests.Session()
    session.headers.update(config.HEADERS)
    return session


def fetch_with_retry(session, url: str, params: Optional[dict] = None,
                     max_retries: Optional[int] = None) -> Optional[requests.Response]:
    """GET with exponential-ish backoff for HTTP 429 and 5xx. Returns None on failure."""
    global _delay_factor
    max_retries = max_retries or config.MAX_RETRIES
    for attempt in range(max_retries):
        try:
            response = session.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            logger.error("Request error (attempt %d/%d): %s", attempt + 1, max_retries, exc)
            time.sleep(random.uniform(2, 5))
            continue

        if response.status_code == 200:
            _delay_factor = max(1.0, _delay_factor * 0.9)
            return response
        if response.status_code == 429 or response.status_code >= 500:
            _delay_factor = min(4.0, _delay_factor * 1.5)   # slow everything down
            wait = (attempt + 1) * random.uniform(5, 10)
            logger.warning("HTTP %s. Sleeping %.1fs (attempt %d/%d)...",
                           response.status_code, wait, attempt + 1, max_retries)
            time.sleep(wait)
            continue
        # 4xx other than 429 (400/403/404/999...) will not fix itself by retrying
        logger.warning("HTTP %s for %s - giving up on this URL.", response.status_code, url)
        return None
    return None


def parse_job_details(session, job_id: str) -> dict:
    """Fetch the detail page for one job: full description + criteria block."""
    details = {
        "job_description": None,
        "seniority_level": None,
        "employment_type": None,
        "job_function": None,
        "industries": None,
    }
    response = fetch_with_retry(session, DETAIL_URL.format(job_id=job_id))
    if not response:
        return details

    soup = BeautifulSoup(response.text, "html.parser")

    desc_div = soup.find("div", class_="show-more-less-html__markup")
    if desc_div:
        details["job_description"] = desc_div.get_text(separator=" ", strip=True)

    for item in soup.find_all("li", class_="description__job-criteria-item"):
        header = item.find("h3", class_="description__job-criteria-subheader")
        value = item.find("span", class_="description__job-criteria-text")
        if not (header and value):
            continue
        h = header.get_text(strip=True).lower()
        v = value.get_text(strip=True)
        if "seniority" in h:
            details["seniority_level"] = v
        elif "employment" in h:
            details["employment_type"] = v
        elif "function" in h:
            details["job_function"] = v
        elif "industries" in h:
            details["industries"] = v
    return details


def _search_params(keyword: str, location: str, offset: int, date_posted,
                   experience_levels, sort_by) -> dict:
    params = {"keywords": keyword, "location": location, "start": offset}
    if date_posted in config.DATE_POSTED_CODES:
        params["f_TPR"] = config.DATE_POSTED_CODES[date_posted]
    if experience_levels:
        params["f_E"] = ",".join(str(x) for x in experience_levels)
    if sort_by in config.SORT_CODES:
        params["sortBy"] = config.SORT_CODES[sort_by]
    return params


def _parse_card(card, keyword: str, location: str) -> Optional[dict]:
    urn = card.get("data-entity-urn", "")
    if not urn or ":" not in urn:
        return None
    title = card.find("h3", class_="base-search-card__title")
    company = card.find("h4", class_="base-search-card__subtitle")
    loc = card.find("span", class_="job-search-card__location")
    date = card.find("time")
    link = card.find("a", class_="base-card__full-link")
    href = link.get("href") if link else None
    return {
        "job_id": urn.split(":")[-1].strip(),
        "search_keyword": keyword,
        "search_location": location,
        "job_title": title.get_text(strip=True) if title else None,
        "company_name": company.get_text(strip=True) if company else None,
        "location": loc.get_text(strip=True) if loc else None,
        "posted_date": date.get("datetime") if date else None,
        "job_url": href.split("?")[0] if href else None,
    }


def scrape_linkedin_jobs(
    keywords: Iterable[str],
    location: Union[str, Iterable[str]],
    pages_per_keyword: int,
    existing_ids: Optional[set] = None,
    date_posted=config.DATE_POSTED,
    experience_levels=config.EXPERIENCE_LEVELS,
    sort_by=config.SORT_BY,
) -> pd.DataFrame:
    """
    Scrape search cards and enrich each NEW posting with its detail page.

    existing_ids: job_ids already in your database - these are skipped BEFORE the
    (slow, block-prone) detail request, which saves most of the requests on re-runs.
    """
    locations = [location] if isinstance(location, str) else list(location)
    global _delay_factor
    _delay_factor = 1.0
    existing_ids = {str(x) for x in (existing_ids or set())}
    seen: set = set()
    jobs: list = []
    skipped_existing = 0
    session = build_session()

    for loc in locations:
        for keyword in keywords:
            logger.info("Extracting '%s' in '%s'...", keyword, loc)
            offset = 0
            for page in range(pages_per_keyword):
                params = _search_params(keyword, loc, offset, date_posted, experience_levels, sort_by)
                response = fetch_with_retry(session, SEARCH_URL, params=params)
                if not response:
                    logger.warning("Search request failed for '%s' / '%s' at offset %d. "
                                   "Skipping the rest of this keyword.", keyword, loc, offset)
                    break

                cards = BeautifulSoup(response.text, "html.parser").find_all("div", class_="base-card")
                if not cards:
                    msg = ("No job cards for '%s' / '%s' at offset %d. Either the results ended, "
                           "LinkedIn blocked the request, or its HTML changed.")
                    (logger.info if page > 0 else logger.warning)(msg, keyword, loc, offset)
                    break
                logger.info("Page %d: %d cards.", page + 1, len(cards))

                for card in cards:
                    record = _parse_card(card, keyword, loc)
                    if record is None:
                        continue
                    jid = record["job_id"]
                    if jid in seen:
                        continue
                    seen.add(jid)
                    if jid in existing_ids:
                        skipped_existing += 1
                        continue

                    _sleep(config.DETAIL_DELAY)
                    record.update(parse_job_details(session, jid))
                    jobs.append(record)

                offset += len(cards)  # advance by what we actually received
                _sleep(config.PAGE_DELAY)

    # One cooling-down pass for jobs whose description request was rate-limited
    failed = [j for j in jobs if not j.get("job_description")]
    if failed:
        logger.info("Retrying %d jobs without a description after a cool-down...", len(failed))
        time.sleep(30)
        for job in failed:
            _sleep(config.DETAIL_DELAY)
            job.update({k: v for k, v in parse_job_details(session, job["job_id"]).items() if v})

    logger.info("Scrape finished: %d new jobs, %d already in DB (skipped).", len(jobs), skipped_existing)
    return pd.DataFrame(jobs, columns=RAW_COLUMNS)
