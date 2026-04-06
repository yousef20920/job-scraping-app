"""
Email notification integration for daily job digests.
"""
import logging
import mimetypes
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
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
        """Send a daily digest email with a short summary and attachments."""
        if not self.enabled:
            logger.info("Skipping email notifications (SMTP/recipient config not present)")
            return False

        try:
            message = EmailMessage()
            today = datetime.now().strftime("%Y-%m-%d")
            message["Subject"] = f"Daily Job Digest - {today}"
            message["From"] = self.email_from
            message["To"] = ", ".join(self.email_to)
            message.set_content(self._build_plain_text_body(jobs))

            for report_path in report_files.values():
                if not report_path or not os.path.exists(report_path):
                    continue
                self._attach_file(message, report_path)

            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=context) as server:
                server.login(self.smtp_username, self.smtp_password)
                server.send_message(message)

            logger.info(
                "Sent daily digest email to %s with %s jobs",
                ", ".join(self.email_to),
                len(jobs),
            )
            return True

        except Exception as exc:
            logger.error("Failed to send daily digest email: %s", exc)
            return False

    def _build_plain_text_body(self, jobs: List[Dict[str, Any]]) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        companies: Dict[str, int] = {}
        for job in jobs:
            company = job.get("company", "Unknown")
            companies[company] = companies.get(company, 0) + 1

        lines = [
            f"Daily Job Digest - {today}",
            "",
            f"Total jobs found: {len(jobs)}",
            f"Companies represented: {len(companies)}",
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
                "The markdown report and JSON export are attached.",
            ]
        )

        return "\n".join(lines)

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
