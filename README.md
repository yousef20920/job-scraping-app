# Job Scraping Application

An advanced job scraping tool that automatically fetches and processes recent job postings from multiple ATS platforms (Greenhouse, Lever, Ashby).

## Features

- **Multi-Platform Fetching**: Scrapes jobs from:
  - Greenhouse API (`https://boards-api.greenhouse.io`)
  - Lever API (`https://api.lever.co`)
  - Ashby GraphQL (`jobs.ashbyhq.com`)

- **Intelligent Processing**:
  - Data normalization across different ATS formats
  - Deduplication based on job title and company
  - Ranking system based on configurable keywords and skills
  - Location normalization

- **Freshness Controls**:
  - Keeps only jobs posted within the last 24 hours by default
  - Drops jobs with missing or unparseable posting dates
  - Optional `ever-jobs` integration is disabled by default

- **Automated Reporting**:
  - JSON output (`data/jobs_agg.json`)
  - Markdown daily reports (`report/YYYY-MM-DD.md`)
  - Optional email digest via Gmail SMTP
  - Automatic GitHub commits
  - Daily digest GitHub issues

- **GitHub Actions Integration**:
  - Runs daily at 9 AM UTC
  - Manual trigger support
  - Automatic artifact upload

## Setup

### 1. Prerequisites

- Python 3.11+
- GitHub repository with Actions enabled
- (Optional) OpenAI API key if you explicitly enable AI analysis

### 2. Installation

```bash
pip install -r requirements.txt
```

### 3. Configuration

#### Companies Configuration (`config/companies.yaml`)

Define companies to scrape:

```yaml
companies:
  - name: "OpenAI"
    ats: "greenhouse"
    board_token: "openai"
  
  - name: "Stripe"
    ats: "greenhouse"
    board_token: "stripe"
```

#### Keywords Configuration (`config/keywords.yaml`)

Configure job filtering and ranking:

```yaml
keywords:
  high_priority:
    - "Senior Software Engineer"
    - "Staff Engineer"
  
preferred_skills:
  - "Python"
  - "React"
```

### 4. GitHub Secrets

Configure these secrets in your repository:

- `GITHUB_TOKEN`: Automatically provided by GitHub Actions (no setup needed)
- `ENABLE_AI_ANALYSIS`: Optional, defaults to `false`
- `OPENAI_API_KEY`: Optional, only used when AI analysis is enabled
- `ENABLE_EVER_JOBS`: Optional, defaults to `false`
- `EVER_JOBS_API_URL`: Optional, only used when `ENABLE_EVER_JOBS=true`
- `JOB_POSTED_WITHIN_HOURS`: Optional, defaults to `24`
- `SMTP_USERNAME`: Your Gmail address
- `SMTP_PASSWORD`: Your Gmail app password
- `EMAIL_TO`: Comma-separated recipient list
- `EMAIL_FROM`: Optional sender address override, usually the same Gmail address
- `SMTP_HOST`: Optional, defaults to `smtp.gmail.com`
- `SMTP_PORT`: Optional, defaults to `465`
- `ENABLE_EMAIL_NOTIFICATIONS`: Optional, defaults to `false`; set to `true` to enable email delivery

## Usage

### Manual Run

```bash
python main.py
```

### GitHub Actions

The workflow runs automatically daily at 9 AM UTC. You can also trigger it manually from the Actions tab.

## Output

### JSON Output (`data/jobs_agg.json`)

Contains all processed jobs with metadata:

```json
{
  "generated_at": "2024-01-15T09:00:00",
  "total_jobs": 150,
  "jobs": [
    {
      "id": "gh_openai_12345",
      "title": "Senior Software Engineer",
      "company": "OpenAI",
      "location": "San Francisco, CA",
      "score": 25.0,
      "url": "https://...",
      ...
    }
  ]
}
```

### Markdown Report (`report/YYYY-MM-DD.md`)

Human-readable daily report with:
- Summary statistics
- Top job opportunities (ranked by score)
- Jobs grouped by company

### GitHub Issue

Automatically creates/updates a "Daily Roles Digest" issue with:
- Summary of jobs found
- Top 10 opportunities
- Links to full reports

### Email Digest

When SMTP settings are configured, the app also sends a daily email summary with:
- total jobs found
- top companies
- top 10 ranked roles
- inline links only, with no attachments

## Architecture

```
┌─────────────────────┐
│  GitHub Actions     │
│  (Daily Schedule)   │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│    main.py          │
│  (Orchestrator)     │
└──────────┬──────────┘
           │
    ┌──────┴──────┬──────────────┬─────────────┐
    ▼             ▼              ▼             ▼
┌────────┐  ┌───────────┐  ┌──────────┐  ┌─────────┐
│Fetchers│  │ Processor │  │ Reporter │  │   AI    │
└────────┘  └───────────┘  └──────────┘  └─────────┘
│           │              │             │
│ Greenhouse│ Normalize    │ JSON        │ Analysis
│ Lever     │ Deduplicate  │ Markdown    │ Resume Tips
│ Ashby     │ Rank         │             │ Interview
└───────────┴──────────────┴─────────────┴──────────
                           │
                           ▼
                  ┌─────────────────┐
                  │ GitHub Integration│
                  │ - Commit/Push   │
                  │ - Create Issues │
                  └─────────────────┘
```

## Modules

- **`fetchers.py`**: Job fetchers for different ATS platforms
- **`processor.py`**: Data normalization, deduplication, and ranking
- **`reporter.py`**: Output generation (JSON and Markdown)
- **`github_integration.py`**: GitHub commits and issue management
- **`ai_assistant.py`**: ChatGPT integration for job analysis
- **`main.py`**: Main orchestration script

## Logging

Logs are stored in `logs/job_scraping_YYYY-MM-DD.log` with detailed information about:
- Fetching progress
- Processing steps
- Errors and warnings
- API calls

## Error Handling

The application includes comprehensive error handling:
- API request failures are logged and don't stop execution
- Missing configuration files raise clear errors
- GitHub operations gracefully handle missing tokens
- AI features degrade gracefully when API key is not configured

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

MIT License
