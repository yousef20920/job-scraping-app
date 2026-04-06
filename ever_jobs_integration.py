"""
Optional integration with an external ever-jobs API instance.
"""
import logging
import os
from typing import Any, Dict, List

import requests

logger = logging.getLogger(__name__)


class EverJobsIntegration:
    """Fetches jobs from a running ever-jobs API and normalizes them."""

    def __init__(self, api_url: str | None = None, api_key: str | None = None):
        self.api_url = (api_url or os.getenv("EVER_JOBS_API_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("EVER_JOBS_API_KEY")
        self.enabled = bool(self.api_url)
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "Job-Scraping-App/1.0",
        })
        if self.api_key:
            self.session.headers["x-api-key"] = self.api_key

    def fetch_all_jobs(self, searches_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Run configured searches against ever-jobs and normalize results."""
        if not self.enabled:
            logger.info("ever-jobs integration disabled - EVER_JOBS_API_URL not configured")
            return []

        if not searches_config.get("enabled", False):
            logger.info("ever-jobs integration disabled in config")
            return []

        default_site_types = searches_config.get("default_site_types", [])
        description_format = searches_config.get("description_format", "plain")
        request_timeout = searches_config.get("request_timeout", 60)
        default_results_wanted = searches_config.get("results_wanted", 35)

        all_jobs: List[Dict[str, Any]] = []
        total_searches = 0

        for country_query in searches_config.get("country_queries", []):
            country = country_query.get("country")
            search_terms = country_query.get("search_terms", [])
            site_types = country_query.get("site_types", default_site_types)
            results_wanted = country_query.get("results_wanted", default_results_wanted)

            for search_term in search_terms:
                total_searches += 1
                payload = {
                    "country": country,
                    "descriptionFormat": description_format,
                    "resultsWanted": results_wanted,
                    "searchTerm": search_term,
                    "siteType": site_types,
                }

                try:
                    jobs = self._search_jobs(payload, request_timeout)
                    logger.info(
                        "ever-jobs search returned %s jobs for term '%s' in %s",
                        len(jobs),
                        search_term,
                        country,
                    )
                    all_jobs.extend(self._normalize_jobs(jobs))
                except requests.exceptions.RequestException as exc:
                    logger.error(
                        "ever-jobs search failed for term '%s' in %s: %s",
                        search_term,
                        country,
                        exc,
                    )
                except Exception as exc:
                    logger.error(
                        "Unexpected ever-jobs error for term '%s' in %s: %s",
                        search_term,
                        country,
                        exc,
                    )

        logger.info(
            "ever-jobs integration completed: %s searches, %s normalized jobs",
            total_searches,
            len(all_jobs),
        )
        return all_jobs

    def _search_jobs(self, payload: Dict[str, Any], request_timeout: int) -> List[Dict[str, Any]]:
        url = f"{self.api_url}/api/jobs/search"
        response = self.session.post(url, json=payload, timeout=request_timeout)
        response.raise_for_status()

        data = response.json()
        if isinstance(data, dict):
            return data.get("jobs", [])
        if isinstance(data, list):
            return data
        return []

    def _normalize_jobs(self, jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []

        for job in jobs:
            job_id = job.get("id") or self._fallback_job_id(job)
            site = job.get("site", "ever_jobs")
            location = self._format_location(job.get("location"))
            apply_url = job.get("jobUrl") or job.get("jobUrlDirect") or ""

            normalized.append({
                "id": f"ever_{site}_{job_id}",
                "title": job.get("title", ""),
                "company": job.get("companyName", ""),
                "location": location,
                "url": apply_url,
                "description": job.get("description", "") or "",
                "date_posted": job.get("datePosted", ""),
                "source": f"ever_jobs:{site}",
                "raw_data": job,
            })

        return normalized

    def _format_location(self, location: Any) -> str:
        if isinstance(location, dict):
            parts = [
                location.get("city"),
                location.get("state"),
                location.get("country"),
            ]
            return ", ".join(part for part in parts if part)
        if isinstance(location, str):
            return location
        return "Unknown"

    def _fallback_job_id(self, job: Dict[str, Any]) -> str:
        title = job.get("title", "unknown")
        company = job.get("companyName", "unknown")
        site = job.get("site", "ever_jobs")
        return f"{site}_{company}_{title}".lower().replace(" ", "_")
