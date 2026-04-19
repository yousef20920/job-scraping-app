#!/usr/bin/env python3
"""
Discover supported ATS boards from the easy-application company list.

This script imports the company list from j-delaney/easy-application, attempts
to detect direct Greenhouse / Lever / Ashby boards from each company's careers
page, and updates local config files with verified matches only.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import requests
import yaml


LOGGER = logging.getLogger("easy_application_discovery")
USER_AGENT = "Job-Scraping-App/1.0"
EASY_APPLICATION_README_URL = (
    "https://raw.githubusercontent.com/j-delaney/easy-application/master/README.md"
)

SUPPORTED_ATS = ("greenhouse", "lever", "ashby")

COMPANY_ROW_RE = re.compile(r"^\| \[(.+?)\]\((.+?)\) \| (.+?) \|$")
GREENHOUSE_RE = re.compile(r"boards\.greenhouse\.io/([^/?#\"'&]+)", re.IGNORECASE)
LEVER_RE = re.compile(
    r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?([^/?#\"'&]+)", re.IGNORECASE
)
ASHBY_PAGE_RE = re.compile(r"jobs\.ashbyhq\.com/([^/?#\"'&]+)", re.IGNORECASE)
ASHBY_API_RE = re.compile(
    r"api\.ashbyhq\.com/posting-api/job-board/([^/?#\"'&]+)", re.IGNORECASE
)
HREF_RE = re.compile(r"""(?:href|src)=["']([^"'#]+)["']""", re.IGNORECASE)


@dataclass(frozen=True)
class CompanySource:
    name: str
    careers_url: str
    location: str


@dataclass
class DiscoveryResult:
    company: str
    careers_url: str
    final_url: str
    ats: str | None
    board_token: str | None = None
    board_url: str | None = None
    source: str | None = None
    error: str | None = None

    def to_company_entry(self) -> dict[str, str] | None:
        if self.ats == "greenhouse" and self.board_token:
            return {
                "name": self.company,
                "ats": "greenhouse",
                "board_token": self.board_token,
            }
        if self.ats in {"lever", "ashby"} and self.board_url:
            return {
                "name": self.company,
                "ats": self.ats,
                "board_url": self.board_url,
            }
        return None


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def normalize_url(value: str) -> str:
    value = value.strip()
    if value.startswith("//"):
        value = "https:" + value
    value = value.replace("https://https://", "https://")
    value = value.replace("http://http://", "http://")
    return value


def parse_easy_application_sources(readme_text: str) -> list[CompanySource]:
    sources: list[CompanySource] = []
    for line in readme_text.splitlines():
        match = COMPANY_ROW_RE.match(line.strip())
        if not match:
            continue
        name, careers_url, location = match.groups()
        sources.append(
            CompanySource(
                name=name.strip(),
                careers_url=normalize_url(careers_url),
                location=location.strip(),
            )
        )
    return sources


def extract_candidate_urls(html: str, base_url: str) -> list[str]:
    candidates = [base_url]
    for raw in HREF_RE.findall(html):
        absolute = normalize_url(urljoin(base_url, raw))
        candidates.append(absolute)
    # Preserve order while deduplicating.
    return list(dict.fromkeys(candidates))


def _greenhouse_result(company: str, careers_url: str, final_url: str, token: str, source: str) -> DiscoveryResult:
    return DiscoveryResult(
        company=company,
        careers_url=careers_url,
        final_url=final_url,
        ats="greenhouse",
        board_token=token,
        source=source,
    )


def _lever_result(company: str, careers_url: str, final_url: str, slug: str, source: str) -> DiscoveryResult:
    return DiscoveryResult(
        company=company,
        careers_url=careers_url,
        final_url=final_url,
        ats="lever",
        board_url=f"https://api.lever.co/v0/postings/{slug}",
        source=source,
    )


def _ashby_result(company: str, careers_url: str, final_url: str, slug: str, source: str) -> DiscoveryResult:
    return DiscoveryResult(
        company=company,
        careers_url=careers_url,
        final_url=final_url,
        ats="ashby",
        board_url=f"https://jobs.ashbyhq.com/{slug}",
        source=source,
    )


def extract_identifier_for_match(result: DiscoveryResult) -> str:
    if result.board_token:
        return result.board_token
    if result.board_url:
        return result.board_url.rstrip("/").split("/")[-1]
    return ""


def is_confident_company_match(result: DiscoveryResult) -> bool:
    company_key = normalize_name(result.company)
    identifier = normalize_name(extract_identifier_for_match(result))
    if not company_key or not identifier:
        return False
    return company_key in identifier or identifier in company_key


def detect_supported_board(
    company: str,
    careers_url: str,
    final_url: str,
    html: str,
) -> DiscoveryResult:
    candidates = extract_candidate_urls(html, final_url)
    searchable_blobs = [
        ("final_url", final_url),
        ("html", html),
        *[(f"link:{candidate}", candidate) for candidate in candidates],
    ]

    for source, text in searchable_blobs:
        match = GREENHOUSE_RE.search(text)
        if match:
            return _greenhouse_result(company, careers_url, final_url, match.group(1), source)

    for source, text in searchable_blobs:
        match = LEVER_RE.search(text)
        if match:
            return _lever_result(company, careers_url, final_url, match.group(1), source)

    for source, text in searchable_blobs:
        match = ASHBY_API_RE.search(text)
        if match:
            return _ashby_result(company, careers_url, final_url, match.group(1), source)

    for source, text in searchable_blobs:
        match = ASHBY_PAGE_RE.search(text)
        if match:
            return _ashby_result(company, careers_url, final_url, match.group(1), source)

    return DiscoveryResult(
        company=company,
        careers_url=careers_url,
        final_url=final_url,
        ats=None,
        error="no supported ATS signature found",
    )


def validate_result(session: requests.Session, result: DiscoveryResult, timeout: int) -> DiscoveryResult:
    if result.ats == "greenhouse" and result.board_token:
        url = f"https://boards-api.greenhouse.io/v1/boards/{result.board_token}/jobs?content=true"
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            response.json()
            if is_confident_company_match(result):
                return result
            result.ats = None
            result.error = "greenhouse validation passed but board appears to belong to a different company"
            result.board_token = None
            return result
        except (requests.RequestException, ValueError) as exc:
            result.ats = None
            result.error = f"greenhouse validation failed: {exc}"
            result.board_token = None
            return result

    if result.ats == "lever" and result.board_url:
        try:
            response = session.get(f"{result.board_url}?mode=json", timeout=timeout)
            response.raise_for_status()
            response.json()
            if is_confident_company_match(result):
                return result
            result.ats = None
            result.error = "lever validation passed but board appears to belong to a different company"
            result.board_url = None
            return result
        except (requests.RequestException, ValueError) as exc:
            result.ats = None
            result.error = f"lever validation failed: {exc}"
            result.board_url = None
            return result

    if result.ats == "ashby" and result.board_url:
        slug = result.board_url.rstrip("/").split("/")[-1]
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            response.json()
            if is_confident_company_match(result):
                return result
            result.ats = None
            result.error = "ashby validation passed but board appears to belong to a different company"
            result.board_url = None
            return result
        except (requests.RequestException, ValueError) as exc:
            result.ats = None
            result.error = f"ashby validation failed: {exc}"
            result.board_url = None
            return result

    return result


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def dump_yaml(path: Path, content: dict, header_comment: str | None = None) -> None:
    with path.open("w", encoding="utf-8") as handle:
        if header_comment:
            handle.write(header_comment.rstrip() + "\n\n")
        yaml.safe_dump(content, handle, sort_keys=False, allow_unicode=True)


def update_targeting_config(targeting_path: Path, sources: Iterable[CompanySource]) -> tuple[int, int]:
    config = load_yaml(targeting_path)
    existing_targets = config.get("target_companies", [])
    seen = {normalize_name(name) for name in existing_targets}
    before = len(existing_targets)

    for source in sources:
        key = normalize_name(source.name)
        if key not in seen:
            existing_targets.append(source.name)
            seen.add(key)

    config["target_companies"] = existing_targets
    dump_yaml(targeting_path, config)
    return before, len(existing_targets)


def update_companies_config(companies_path: Path, results: Iterable[DiscoveryResult]) -> tuple[int, int, list[str]]:
    config = load_yaml(companies_path)
    companies = config.get("companies", [])
    seen = {normalize_name(entry["name"]) for entry in companies}
    before = len(companies)
    added_names: list[str] = []

    for result in results:
        entry = result.to_company_entry()
        if not entry:
            continue
        key = normalize_name(result.company)
        if key in seen:
            continue
        companies.append(entry)
        seen.add(key)
        added_names.append(result.company)

    config["companies"] = companies
    dump_yaml(
        companies_path,
        config,
        header_comment=(
            "# ATS-first company seeds for direct fetching.\n"
            "# Imported supported ATS links from the easy-application company list where possible."
        ),
    )
    return before, len(companies), added_names


def fetch_company_page(session: requests.Session, source: CompanySource, timeout: int) -> DiscoveryResult:
    try:
        response = session.get(source.careers_url, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
    except requests.RequestException as exc:
        return DiscoveryResult(
            company=source.name,
            careers_url=source.careers_url,
            final_url=source.careers_url,
            ats=None,
            error=str(exc),
        )

    final_url = normalize_url(response.url)
    html = response.text or ""
    result = detect_supported_board(source.name, source.careers_url, final_url, html)
    if result.ats in SUPPORTED_ATS:
        return validate_result(session, result, timeout)
    return result


def summarize_results(results: Iterable[DiscoveryResult]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for result in results:
        counter[result.ats or "unresolved"] += 1
    return dict(counter)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write discovered companies into config files.",
    )
    parser.add_argument(
        "--report-path",
        default="data/easy_application_discovery.json",
        help="Path for a JSON discovery report.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on the number of companies to inspect.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="Per-request timeout in seconds.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=16,
        help="Number of concurrent page fetches.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    readme_response = session.get(EASY_APPLICATION_README_URL, timeout=args.timeout)
    readme_response.raise_for_status()
    sources = parse_easy_application_sources(readme_response.text)
    if args.limit is not None:
        sources = sources[: args.limit]

    results: list[DiscoveryResult] = []
    with ThreadPoolExecutor(max_workers=max(args.workers, 1)) as executor:
        futures = {
            executor.submit(fetch_company_page, session, source, args.timeout): (index, source)
            for index, source in enumerate(sources, start=1)
        }
        for future in as_completed(futures):
            index, source = futures[future]
            LOGGER.info("[%s/%s] Inspecting %s", index, len(sources), source.name)
            results.append(future.result())

    results.sort(key=lambda result: normalize_name(result.company))

    summary = summarize_results(results)

    report_path = Path(args.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "inspected": len(results),
                "summary": summary,
                "results": [result.__dict__ for result in results],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(json.dumps({"inspected": len(results), "summary": summary}, indent=2))

    if args.write:
        targeting_before, targeting_after = update_targeting_config(
            Path("config/targeting.yaml"),
            sources,
        )
        companies_before, companies_after, added_names = update_companies_config(
            Path("config/companies.yaml"),
            [result for result in results if result.ats in SUPPORTED_ATS],
        )

        print(
            json.dumps(
                {
                    "target_companies_before": targeting_before,
                    "target_companies_after": targeting_after,
                    "direct_companies_before": companies_before,
                    "direct_companies_after": companies_after,
                    "new_direct_company_names": added_names,
                },
                indent=2,
            )
        )

    unresolved = sum(1 for result in results if result.ats is None)
    if unresolved:
        LOGGER.info("%s companies remain unresolved for direct ATS scraping", unresolved)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
