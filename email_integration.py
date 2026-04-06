"""
Email notification integration for daily job digests.
"""
import hashlib
import json
import logging
import mimetypes
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Any

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
        if not _env_truthy(os.getenv("ENABLE_EMAIL_NOTIFICATIONS", "true")):
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
        report_files: Dict[str, str],
    ) -> bool:
        """Send a daily digest email for jobs that have not been emailed before."""
        if not self.enabled:
            logger.info("Skipping email notifications (SMTP/recipient config not present)")
            return False

        try:
            sent_state = self._load_sent_state()
            new_jobs = self._filter_new_jobs(jobs, sent_state)

            if not new_jobs:
                logger.info("Skipping email digest - no new jobs since last notification")
                return False

            message = EmailMessage()
            now = datetime.now()
            timestamp = now.strftime("%Y-%m-%d %H:%M")
            message["Subject"] = f"New Job Digest - {timestamp} ({len(new_jobs)} new)"
            message["From"] = self.email_from
            message["To"] = ", ".join(self.email_to)
            message.set_content(self._build_plain_text_body(new_jobs, len(jobs)))

            self._attach_text(
                message,
                self._build_markdown_attachment(new_jobs, len(jobs)),
                "new_jobs.md",
                "text",
                "markdown",
            )
            self._attach_text(
                message,
                json.dumps(
                    {
                        "generated_at": now.isoformat(),
                        "total_current_jobs": len(jobs),
                        "new_jobs_count": len(new_jobs),
                        "jobs": new_jobs,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                "new_jobs.json",
                "application",
                "json",
            )

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
                "Attached files contain the new-jobs-only export.",
            ]
        )

        return "\n".join(lines)

    def _build_markdown_attachment(self, jobs: List[Dict[str, Any]], total_jobs: int) -> str:
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "# New Job Digest",
            "",
            f"Generated: {generated_at}",
            "",
            f"- New jobs: {len(jobs)}",
            f"- Total current matches: {total_jobs}",
            "",
        ]

        for index, job in enumerate(jobs, start=1):
            lines.extend(
                [
                    f"## {index}. {job.get('title', 'N/A')}",
                    "",
                    f"- Company: {job.get('company', 'N/A')}",
                    f"- Location: {job.get('location', 'N/A')}",
                    f"- Score: {job.get('score', 0):.1f}",
                    f"- Source: {job.get('source', 'N/A')}",
                    f"- Apply: {job.get('url', 'N/A')}",
                    "",
                ]
            )

        return "\n".join(lines)

    def _job_fingerprint(self, job: Dict[str, Any]) -> str:
        job_id = str(job.get("id", "")).strip()
        if job_id:
            return job_id

        raw_value = " | ".join(
            [
                str(job.get("company", "")).strip().lower(),
                str(job.get("title", "")).strip().lower(),
                str(job.get("location", "")).strip().lower(),
                str(job.get("url", "")).strip().lower(),
                str(job.get("source", "")).strip().lower(),
            ]
        )
        return hashlib.sha256(raw_value.encode("utf-8")).hexdigest()

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
            fingerprint = self._job_fingerprint(job)
            if fingerprint not in sent_state:
                new_jobs.append(job)
        return new_jobs

    def _update_sent_state(self, sent_state: Dict[str, str], jobs: List[Dict[str, Any]]) -> None:
        now = datetime.now().isoformat()
        for job in jobs:
            sent_state[self._job_fingerprint(job)] = now

        if len(sent_state) > self.state_limit:
            sorted_items = sorted(sent_state.items(), key=lambda item: item[1], reverse=True)
            sent_state = dict(sorted_items[: self.state_limit])

        self._save_sent_state(sent_state)

    def _attach_text(
        self,
        message: EmailMessage,
        content: str,
        filename: str,
        maintype: str,
        subtype: str,
    ) -> None:
        message.add_attachment(
            content.encode("utf-8"),
            maintype=maintype,
            subtype=subtype,
            filename=filename,
        )

    def _attach_file(self, message: EmailMessage, report_path: str) -> None:
        mime_type, _ = mimetypes.guess_type(report_path)
        if mime_type:
            maintype, subtype = mime_type.split("/", 1)
        else:
            maintype, subtype = "application", "octet-stream"

        with open(report_path, "rb") as report_file:
            message.add_attachment(
                report_file.read(),
                maintype=maintype,
                subtype=subtype,
                filename=os.path.basename(report_path),
            )
