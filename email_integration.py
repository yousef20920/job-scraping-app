"""
Email notification integration for daily job digests.
"""
import hashlib
import json
import logging
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Any
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)


def _env_truthy(value: str | None) -> bool:
    if value is None:
        return False

    return value.strip().lower() in {"1", "true", "yes", "on"}


class EmailNotifier:
    """Send daily digest emails through SMTP."""

    def __init__(self):
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "465"))
        self.smtp_username = os.getenv("SMTP_USERNAME", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.email_from = os.getenv("EMAIL_FROM", self.smtp_username)
        self.email_to = self._parse_recipients(os.getenv("EMAIL_TO", ""))
        self.state_file = Path(os.getenv("EMAIL_SENT_STATE_FILE", "data/emailed_jobs.json"))
        self.state_limit = int(os.getenv("EMAIL_SENT_STATE_LIMIT", "10000"))
        self.enabled = self._is_enabled()

    def _is_enabled(self) -> bool:
        if not _env_truthy(os.getenv("ENABLE_EMAIL_NOTIFICATIONS", "false")):
            return False

        required_values = [
            self.smtp_host,
            self.smtp_port,
            self.smtp_username,
            self.smtp_password,
            self.email_from,
        ]
        return all(required_values) and bool(self.email_to)

    def _parse_recipients(self, raw_recipients: str) -> List[str]:
        return [item.strip() for item in raw_recipients.split(",") if item.strip()]

    def send_daily_digest(
        self,
        jobs: List[Dict[str, Any]],
        _report_files: Dict[str, str],
    ) -> bool:
        """Send a daily digest email for jobs that have not been emailed before."""
        if not self.enabled:
            logger.info("Skipping email notifications (SMTP/recipient config not present)")
            return False

        try:
            sent_state = self._load_sent_state()
            initial_state_size = len(sent_state)
            self._hydrate_sent_aliases(sent_state, jobs)
            new_jobs = self._filter_new_jobs(jobs, sent_state)

            if not new_jobs:
                if len(sent_state) != initial_state_size:
                    self._save_sent_state(sent_state)
                logger.info("Skipping email digest - no new jobs since last notification")
                return False

            message = EmailMessage()
            now = datetime.now()
            timestamp = now.strftime("%Y-%m-%d %H:%M")
            message["Subject"] = f"New Job Digest - {timestamp} ({len(new_jobs)} new)"
            message["From"] = self.email_from
            message["To"] = ", ".join(self.email_to)
            message.set_content(self._build_plain_text_body(new_jobs, len(jobs)))

            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=context) as server:
                server.login(self.smtp_username, self.smtp_password)
                server.send_message(message)

            self._update_sent_state(sent_state, new_jobs)
            logger.info(
                "Sent daily digest email to %s with %s new jobs",
                ", ".join(self.email_to),
                len(new_jobs),
            )
            return True

        except Exception as exc:
            logger.error("Failed to send daily digest email: %s", exc)
            return False

    def _build_plain_text_body(self, jobs: List[Dict[str, Any]], total_jobs: int) -> str:
        today = datetime.now().strftime("%Y-%m-%d %H:%M")
        companies: Dict[str, int] = {}
        for job in jobs:
            company = job.get("company", "Unknown")
            companies[company] = companies.get(company, 0) + 1

        lines = [
            f"New Job Digest - {today}",
            "",
            f"New jobs found: {len(jobs)}",
            f"Total current matches in tracker: {total_jobs}",
            f"Companies represented in new jobs: {len(companies)}",
            "",
            "Top companies:",
        ]

        for company, count in sorted(companies.items(), key=lambda item: item[1], reverse=True)[:5]:
            lines.append(f"- {company}: {count}")

        lines.extend(["", "Top opportunities:"])
        for index, job in enumerate(jobs[:10], start=1):
            lines.append(
                f"{index}. {job.get('title', 'N/A')} at {job.get('company', 'N/A')} | "
                f"{job.get('location', 'N/A')} | score {job.get('score', 0):.1f}"
            )
            lines.append(f"   {job.get('url', 'N/A')}")

        lines.extend(
            [
                "",
                "Only jobs that have not been emailed before are included.",
                "This email includes the full digest inline with no attachments.",
            ]
        )

        return "\n".join(lines)

    def _normalize_text(self, value: Any) -> str:
        return " ".join(str(value or "").strip().lower().split())

    def _normalize_location(self, value: Any) -> str:
        normalized = self._normalize_text(value)
        replacements = {
            "new york, ny (hq)": "new york, ny",
            "remote, us": "remote",
            "remote - us": "remote",
        }
        return replacements.get(normalized, normalized)

    def _canonical_url(self, value: Any) -> str:
        raw_url = str(value or "").strip()
        if not raw_url:
            return ""

        parsed = urlsplit(raw_url)
        path = parsed.path.rstrip("/")
        if parsed.netloc.endswith("boards.greenhouse.io"):
            return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
        if parsed.netloc.endswith("jobs.lever.co"):
            return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
        if parsed.netloc.endswith("jobs.ashbyhq.com"):
            return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

    def _hash_fingerprint_parts(self, *parts: str) -> str:
        raw_value = " | ".join(parts)
        return hashlib.sha256(raw_value.encode("utf-8")).hexdigest()

    def _job_fingerprints(self, job: Dict[str, Any]) -> set[str]:
        company = self._normalize_text(job.get("company"))
        title = self._normalize_text(job.get("title"))
        location = self._normalize_location(job.get("location"))
        canonical_url = self._canonical_url(job.get("url"))
        source = self._normalize_text(job.get("source"))
        fingerprints: set[str] = set()

        job_id = str(job.get("id", "")).strip()
        if job_id:
            fingerprints.add(f"id:{job_id}")

        if company and title:
            fingerprints.add(
                "role:"
                + self._hash_fingerprint_parts(company, title, location)
            )
            fingerprints.add(
                "role_nolocation:"
                + self._hash_fingerprint_parts(company, title)
            )

        if company and title and canonical_url:
            fingerprints.add(
                "url_role:"
                + self._hash_fingerprint_parts(company, title, canonical_url)
            )

        if canonical_url:
            fingerprints.add("url:" + canonical_url.lower())

        if company and title and source:
            fingerprints.add(
                "source_role:"
                + self._hash_fingerprint_parts(company, title, source, location)
            )

        if fingerprints:
            return fingerprints

        fingerprints.add(
            "fallback:"
            + self._hash_fingerprint_parts(
                company,
                title,
                location,
                canonical_url,
                source,
            )
        )
        return fingerprints

    def _load_sent_state(self) -> Dict[str, str]:
        if not self.state_file.exists():
            return {}

        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read email state file %s: %s", self.state_file, exc)
            return {}

    def _save_sent_state(self, state: Dict[str, str]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

    def _filter_new_jobs(
        self,
        jobs: List[Dict[str, Any]],
        sent_state: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        new_jobs: List[Dict[str, Any]] = []
        for job in jobs:
            fingerprints = self._job_fingerprints(job)
            if not any(fingerprint in sent_state for fingerprint in fingerprints):
                new_jobs.append(job)
        return new_jobs

    def _hydrate_sent_aliases(
        self,
        sent_state: Dict[str, str],
        jobs: List[Dict[str, Any]],
    ) -> None:
        for job in jobs:
            job_id = str(job.get("id", "")).strip()
            if not job_id:
                continue

            legacy_keys = [job_id, f"id:{job_id}"]
            existing_timestamp = next(
                (sent_state[key] for key in legacy_keys if key in sent_state),
                None,
            )
            if not existing_timestamp:
                continue

            for fingerprint in self._job_fingerprints(job):
                sent_state.setdefault(fingerprint, existing_timestamp)

    def _update_sent_state(self, sent_state: Dict[str, str], jobs: List[Dict[str, Any]]) -> None:
        now = datetime.now().isoformat()
        for job in jobs:
            for fingerprint in self._job_fingerprints(job):
                sent_state[fingerprint] = now

        if len(sent_state) > self.state_limit:
            sorted_items = sorted(sent_state.items(), key=lambda item: item[1], reverse=True)
            sent_state = dict(sorted_items[: self.state_limit])

        self._save_sent_state(sent_state)
