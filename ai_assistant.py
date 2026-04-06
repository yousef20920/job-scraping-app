"""
AI assistant for targeted job-fit analysis.
"""
import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class AIAssistant:
    """OpenAI integration for concise internship and new-grad job analysis."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.api_url = "https://api.openai.com/v1/chat/completions"
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    def _call_chatgpt(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 900,
    ) -> Optional[str]:
        """Make an OpenAI Chat Completions API call."""
        if not self.api_key:
            logger.warning("OpenAI API key not configured - skipping AI analysis")
            return None

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            data = {
                "model": self.model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            }

            response = requests.post(self.api_url, headers=headers, json=data, timeout=45)
            response.raise_for_status()

            payload = response.json()
            return payload["choices"][0]["message"]["content"]

        except requests.exceptions.RequestException as exc:
            logger.error("Error calling OpenAI API: %s", exc)
            return None
        except Exception as exc:
            logger.error("Unexpected error in OpenAI call: %s", exc)
            return None

    def analyze_job_description(self, job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Generate a focused job-fit analysis for a single role."""
        if not self.api_key:
            return None

        title = job.get("title", "")
        company = job.get("company", "")
        location = job.get("location", "")
        description = job.get("description", "")[:3500]

        messages = [
            {
                "role": "system",
                "content": (
                    "You analyze internship and new-grad software roles. "
                    "Return valid JSON only. Do not include cover-letter advice "
                    "or interview preparation. Keep outputs concise and concrete."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Analyze this job posting for a software/ML internship or new-grad applicant.\n\n"
                    f"Job title: {title}\n"
                    f"Company: {company}\n"
                    f"Location: {location}\n"
                    f"Description:\n{description}\n\n"
                    "Return a JSON object with these keys:\n"
                    "- fit_summary: 2-4 short sentences on why this role matters for the target candidate\n"
                    "- key_requirements: array of 4-8 concise bullets\n"
                    "- nice_to_have: array of 0-5 concise bullets\n"
                    "- resume_tips: array of 4-6 concise bullets focused on what to emphasize\n"
                    "- red_flags: array of 0-4 concise bullets, especially degree/experience restrictions\n"
                    "- seniority_assessment: one of ['internship','new_grad','ambiguous']\n"
                    "- role_focus: one short label such as 'backend', 'frontend', 'full-stack', 'ml', 'data', 'security', 'mobile'\n"
                ),
            },
        ]

        response_text = self._call_chatgpt(messages)
        if not response_text:
            return None

        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError:
            logger.error("AI response was not valid JSON for %s at %s", title, company)
            return None

        return {
            "job_id": job.get("id"),
            "title": title,
            "company": company,
            "location": location,
            "analysis": parsed,
        }

    def analyze_top_jobs(self, jobs: List[Dict[str, Any]], top_n: int = 5) -> Dict[str, Any]:
        """Analyze the top-ranked jobs and return structured insights."""
        if not self.api_key:
            logger.info("OpenAI API key not configured - skipping AI analysis")
            return {
                "enabled": False,
                "message": "AI analysis disabled - configure OPENAI_API_KEY to enable",
            }

        analyses = []
        for job in jobs[:top_n]:
            logger.info("Analyzing job: %s at %s", job.get("title"), job.get("company"))
            analysis = self.analyze_job_description(job)
            if analysis:
                analyses.append(analysis)

        return {
            "enabled": True,
            "model": self.model,
            "total_analyzed": len(analyses),
            "analyses": analyses,
        }
