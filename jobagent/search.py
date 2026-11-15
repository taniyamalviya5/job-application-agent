"""Step 1 - Search: collect jobs from job boards with JobSpy."""
from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Any, Iterable

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
BAD_EMAIL_PARTS = ("noreply", "no-reply", "example.com", ".png", ".jpg")


def _clean(v: Any) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v).strip()


def job_id(title: str, company: str, url: str) -> str:
    """Stable id, so the same job found twice is stored once."""
    key = f"{title.lower().strip()}|{company.lower().strip()}" if company else url
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def extract_emails(*texts: str) -> list[str]:
    found: list[str] = []
    for t in texts:
        for e in EMAIL_RE.findall(t or ""):
            e = e.strip(".").lower()
            if not any(b in e for b in BAD_EMAIL_PARTS) and e not in found:
                found.append(e)
    return found


def normalize(row: dict[str, Any]) -> dict[str, Any]:
    title, company = _clean(row.get("title")), _clean(row.get("company"))
    url = _clean(row.get("job_url_direct")) or _clean(row.get("job_url"))
    desc = _clean(row.get("description"))
    raw_emails = row.get("emails")
    emails_text = ", ".join(raw_emails) if isinstance(raw_emails, (list, tuple)) else _clean(raw_emails)
    return {
        "id": job_id(title, company, url),
        "site": _clean(row.get("site")),
        "title": title,
        "company": company,
        "location": _clean(row.get("location")),
        "job_url": _clean(row.get("job_url")) or url,
        "apply_url": url,
        "date_posted": _clean(row.get("date_posted")),
        "experience": _clean(row.get("experience_range")),
        "skills": _clean(row.get("skills")),
        "emails": extract_emails(emails_text, desc),
        "description": desc[:8000],
    }


def is_excluded(job: dict, exclude_keywords: Iterable[str]) -> bool:
    title = job["title"].lower()
    return any(k.lower() in title for k in exclude_keywords)


def search_jobs(cfg: dict[str, Any], scrape=None) -> list[dict[str, Any]]:
    """Run every (term x location x site) search. One failing site never stops the run."""
    if scrape is None:
        from jobspy import scrape_jobs as scrape  # imported lazily: heavy dependency
    s = cfg["search"]
    jobs: dict[str, dict] = {}
    for term in s["terms"]:
        for loc in s["locations"]:
            for site in s["sites"]:
                try:
                    df = scrape(
                        site_name=[site], search_term=term, location=loc,
                        is_remote=loc.lower() == "remote",
                        results_wanted=s["results_per_search"], hours_old=s["hours_old"],
                        country_indeed=s["country_indeed"], fetch_description=True, verbose=0,
                    )
                    rows = df.to_dict("records") if hasattr(df, "to_dict") else list(df)
                    log.info("%s | %s | %s -> %d jobs", site, term, loc, len(rows))
                except Exception as e:  # site blocked, layout changed, network...
                    log.warning("%s | %s | %s failed: %s", site, term, loc, e)
                    continue
                for row in rows:
                    j = normalize(row)
                    if j["title"] and not is_excluded(j, s["exclude_keywords"]):
                        jobs.setdefault(j["id"], j)
    return list(jobs.values())
