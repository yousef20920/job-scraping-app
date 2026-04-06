"""
Job data processing module - filtering, normalization, deduplication, and ranking.
"""
import hashlib
import logging
import re
from datetime import datetime
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

REMOTE_TERMS = ("remote", "work from home", "wfh", "distributed", "anywhere")
FOREIGN_LOCATION_MARKERS = {
    "india", "united kingdom", "uk", "ireland", "germany", "france", "spain",
    "poland", "netherlands", "sweden", "norway", "denmark", "finland",
    "singapore", "japan", "australia", "new zealand", "mexico", "brazil",
    "argentina", "chile", "colombia", "peru", "philippines", "vietnam",
}

US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "HI",
    "IA", "ID", "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME", "MI", "MN",
    "MO", "MS", "MT", "NC", "ND", "NE", "NH", "NJ", "NM", "NV", "NY", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VA", "VT", "WA",
    "WI", "WV", "WY",
}

US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "district of columbia", "florida", "georgia",
    "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
    "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire",
    "new jersey", "new mexico", "new york", "north carolina", "north dakota",
    "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
    "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
    "virginia", "washington", "west virginia", "wisconsin", "wyoming",
}

CANADA_PROVINCE_CODES = {
    "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT",
}

CANADA_PROVINCE_NAMES = {
    "alberta", "british columbia", "manitoba", "new brunswick",
    "newfoundland", "newfoundland and labrador", "nova scotia", "ontario",
    "prince edward island", "quebec", "saskatchewan", "northwest territories",
    "nunavut", "yukon",
}


class JobProcessor:
    """Processes job data - filters, normalizes, deduplicates, and ranks."""

    def __init__(
        self,
        keywords_config: Dict[str, Any],
        targeting_config: Dict[str, Any] | None = None,
    ):
        self.keywords_config = keywords_config
        self.targeting_config = targeting_config or {}

        keywords_section = keywords_config.get("keywords", {})
        self.high_priority_keywords = keywords_section.get("high_priority", [])
        self.medium_priority_keywords = keywords_section.get("medium_priority", [])
        self.low_priority_keywords = keywords_section.get("low_priority", [])
        self.preferred_skills = keywords_config.get("preferred_skills", [])
        self.preferred_locations = keywords_config.get("preferred_locations", [])
        self.exclude_keywords = keywords_config.get("exclude_keywords", [])

        role_filters = self.targeting_config.get("role_filters", {})
        self.tech_role_keywords = role_filters.get("tech_role_keywords", [])
        self.internship_keywords = role_filters.get("internship_keywords", [])
        self.new_grad_keywords = role_filters.get("new_grad_keywords", [])
        self.exclude_title_keywords = role_filters.get("exclude_title_keywords", [])
        self.exclude_text_keywords = role_filters.get("exclude_text_keywords", [])

        self.target_company_names = self.targeting_config.get("target_companies", [])
        self.company_aliases = self.targeting_config.get("company_aliases", {})
        self.allowed_countries = [
            country.lower()
            for country in self.targeting_config.get("location_filters", {}).get("allowed_countries", [])
        ]
        self.allow_remote_without_region = self.targeting_config.get(
            "location_filters", {}
        ).get("allow_remote_without_region", True)

        self.normalized_target_companies = self._build_target_company_set()

    def _build_target_company_set(self) -> set[str]:
        names: set[str] = set()

        for company in self.target_company_names:
            names.add(self.normalize_text(company))

        for company, aliases in self.company_aliases.items():
            names.add(self.normalize_text(company))
            for alias in aliases:
                names.add(self.normalize_text(alias))

        return names

    def normalize_text(self, text: str) -> str:
        text = (text or "").lower().strip()
        text = re.sub(r"&", " and ", text)
        text = re.sub(r"[^\w\s]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def normalize_location(self, location: str) -> str:
        """Normalize location string."""
        if not location:
            return "Unknown"

        location = location.strip()
        lowered = location.lower()

        if any(remote_term in lowered for remote_term in REMOTE_TERMS):
            return "Remote" if lowered == "remote" else location

        return location

    def is_target_company(self, job: Dict[str, Any]) -> bool:
        """Check whether a company is in the configured allowlist."""
        if not self.normalized_target_companies:
            return True

        normalized_company = self.normalize_text(job.get("company", ""))
        if not normalized_company:
            return False

        if normalized_company in self.normalized_target_companies:
            return True

        return any(
            candidate in normalized_company or normalized_company in candidate
            for candidate in self.normalized_target_companies
        )

    def is_target_role(self, job: Dict[str, Any]) -> bool:
        """Check whether a role is an internship/new-grad tech role."""
        title = job.get("title", "").lower()

        if any(keyword in title for keyword in self.exclude_title_keywords):
            return False

        has_tech_signal = any(keyword in title for keyword in self.tech_role_keywords)
        has_intern_signal = any(keyword in title for keyword in self.internship_keywords)
        has_new_grad_signal = any(keyword in title for keyword in self.new_grad_keywords)

        return has_tech_signal and (has_intern_signal or has_new_grad_signal)

    def is_allowed_location(self, job: Dict[str, Any]) -> bool:
        """Allow only US/Canada locations and safe remote roles."""
        if not self.allowed_countries:
            return True

        location = (job.get("location") or "").strip()
        if not location or location == "Unknown":
            return False

        lowered = location.lower()

        if any(country in lowered for country in self.allowed_countries):
            return True

        if lowered.startswith("us-") or " usa" in lowered or " united states" in lowered:
            return True

        if lowered.startswith("ca-") or " canada" in lowered:
            return True

        if any(province in lowered for province in CANADA_PROVINCE_NAMES):
            return True

        if any(state in lowered for state in US_STATE_NAMES):
            return True

        if self._contains_region_code(location, CANADA_PROVINCE_CODES):
            return True

        if self._contains_region_code(location, US_STATE_CODES):
            return True

        if any(remote_term in lowered for remote_term in REMOTE_TERMS):
            if any(marker in lowered for marker in FOREIGN_LOCATION_MARKERS):
                return False
            return self.allow_remote_without_region

        return False

    def _contains_region_code(self, location: str, codes: set[str]) -> bool:
        upper_location = location.upper()
        return any(
            re.search(rf"(?:,\s*|\b){code}(?:\b|[-,/])", upper_location)
            for code in codes
        )

    def should_exclude_job(self, job: Dict[str, Any]) -> bool:
        """Check if job should be excluded based on company, role, location, and keywords."""
        title = job.get("title", "")

        if not self.is_target_company(job):
            logger.debug("Excluding job '%s' - company not in target list", title)
            return True

        if not self.is_target_role(job):
            logger.debug("Excluding job '%s' - role not internship/new-grad tech target", title)
            return True

        if not self.is_allowed_location(job):
            logger.debug("Excluding job '%s' - location outside US/Canada filter", title)
            return True

        text_to_check = f"{job.get('title', '')} {job.get('description', '')}".lower()
        for exclude_kw in self.exclude_text_keywords:
            if exclude_kw.lower() in text_to_check:
                logger.debug("Excluding job '%s' - role filter text match '%s'", title, exclude_kw)
                return True

        for exclude_kw in self.exclude_keywords:
            if exclude_kw.lower() in text_to_check:
                logger.debug("Excluding job '%s' - contains '%s'", title, exclude_kw)
                return True

        return False

    def calculate_job_score(self, job: Dict[str, Any]) -> float:
        """Calculate relevance score for a job posting."""
        score = 0.0

        title = job.get("title", "").lower()
        description = job.get("description", "").lower()
        location = job.get("location", "").lower()

        for keyword in self.high_priority_keywords:
            if keyword.lower() in title:
                score += 12.0
                break

        for keyword in self.medium_priority_keywords:
            if keyword.lower() in title:
                score += 7.0
                break

        for keyword in self.low_priority_keywords:
            if keyword.lower() in title:
                score += 3.0
                break

        skills_matched = 0
        for skill in self.preferred_skills:
            if skill.lower() in description or skill.lower() in title:
                skills_matched += 1
                score += 1.0

        if skills_matched >= 6:
            score += 5.0
        elif skills_matched >= 3:
            score += 2.0

        for pref_loc in self.preferred_locations:
            if pref_loc.lower() in location:
                score += 2.0
                break

        if self.is_target_company(job):
            score += 2.0

        score += 1.0
        return score

    def generate_job_hash(self, job: Dict[str, Any]) -> str:
        """Generate a hash for deduplication based on company, title, and location."""
        title = self.normalize_text(job.get("title", ""))
        company = self.normalize_text(job.get("company", ""))
        location = self.normalize_text(job.get("location", ""))

        hash_string = f"{company}:{title}:{location}"
        return hashlib.md5(hash_string.encode()).hexdigest()

    def deduplicate_jobs(self, jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate job postings while preserving location variants."""
        seen_hashes: Dict[str, Dict[str, Any]] = {}
        unique_jobs: List[Dict[str, Any]] = []

        for job in jobs:
            job_hash = self.generate_job_hash(job)

            if job_hash not in seen_hashes:
                seen_hashes[job_hash] = job
                unique_jobs.append(job)
                continue

            existing = seen_hashes[job_hash]
            existing_signal = len(existing.get("description", "")) + int(bool(existing.get("url")))
            candidate_signal = len(job.get("description", "")) + int(bool(job.get("url")))

            if candidate_signal > existing_signal:
                seen_hashes[job_hash] = job
                unique_jobs = [entry for entry in unique_jobs if self.generate_job_hash(entry) != job_hash]
                unique_jobs.append(job)

        logger.info("Deduplicated %s jobs to %s unique jobs", len(jobs), len(unique_jobs))
        return unique_jobs

    def process_jobs(self, jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Main processing pipeline."""
        logger.info("Processing %s jobs", len(jobs))

        processed_jobs = []

        for job in jobs:
            job["location"] = self.normalize_location(job.get("location", ""))

            if self.should_exclude_job(job):
                continue

            job["score"] = self.calculate_job_score(job)
            job["processed_at"] = datetime.now().isoformat()
            processed_jobs.append(job)

        processed_jobs = self.deduplicate_jobs(processed_jobs)
        processed_jobs.sort(key=lambda item: item.get("score", 0), reverse=True)

        logger.info(
            "Processing complete: %s jobs after company, role, location, and dedupe filters",
            len(processed_jobs),
        )
        return processed_jobs
