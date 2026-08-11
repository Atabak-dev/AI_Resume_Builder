# Job Application Pipeline

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An interactive CLI that turns a pasted job advert into a tailored, print-ready **CV** and
**cover letter** (Markdown + PDF), using any OpenAI-compatible LLM endpoint.

Its defining constraint: **your identity never reaches the LLM.** Every string in
`personal_info.json` is stripped from the job description before the first API call, and your
name, phone, e-mail and location are re-inserted locally into the generated documents.

One run = one application. Output lands in a timestamped folder, together with a compatibility
score and a list of skills the advert asks for that your CV does not cover.

---

## Contents

- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Your data files](#your-data-files)
- [Configuration](#configuration)
- [Running the pipeline](#running-the-pipeline)
- [Output](#output)
- [Extra scripts](#extra-scripts)
- [Customising the documents](#customising-the-documents)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Contributing](#contributing)
- [License](#license)

---

## How it works

```
paste job advert  ─┐
                   ├─► scrub personal info ─► LLM: extract JobInfo ──┐
company name ──────┘                                                 │
       │                                                             ▼
       └─► Wikipedia lookup ─► LLM: extract CompanyInfo ─────► LLM: write CV  ─► Markdown ─► HTML ─► PDF
                                                          │
                                                          ├──► LLM: score CV ─► missing_skills.txt
                                                          │
                                                          └──► LLM: write cover letter ─► Markdown ─► HTML ─► PDF
```

1. **Input** — you paste the job description into stdin (blank line to finish) and type the
   company name. Nothing is fetched from job boards.
2. **Scrub** — `_remove_personal_info()` removes every leaf string of `personal_info.json` from
   the advert text, case-insensitively, including `word_with_underscores` variants.
3. **Extract** — a JSON-schema-constrained call fills the Pydantic models `JobInfo` and
   `CompanyInfo` (company data comes from the Wikipedia article you name).
4. **Generate** — the LLM writes the CV and the cover letter as Markdown in a fixed dialect;
   a hand-written parser converts that to HTML, and WeasyPrint renders A4 PDFs.
5. **Score** — the generated CV is graded against the advert (0–100) and the gaps are written to
   `missing_skills.txt`.

---

## Requirements

| | |
| --- | --- |
| Python | 3.12 or newer |
| LLM | any endpoint speaking the OpenAI `/chat/completions` API, with `response_format: json_schema` support |
| PDF | WeasyPrint + its native GTK/Pango libraries (see [PDF dependencies](#pdf-dependencies)) |

---

## Installation

```powershell
git clone https://github.com/<your-user>/<your-repo>.git
cd <your-repo>

python -m venv .venv
.\.venv\Scripts\activate          # Windows
# source .venv/bin/activate       # macOS / Linux

pip install --upgrade pip
pip install -r requirements.txt
```

Then create your `.env`:

```powershell
copy .env.example .env            # cp .env.example .env on macOS / Linux
```

and put your key and endpoint in it:

```ini
LLM_API_KEY=your-api-key-here
LLM_MODEL=your-model-name
LLM_HOST=your.llm.host
LLM_BASE_PATH=/v1/chat/completions
LLM_ENDPOINT=https://your.llm.host
```

`.env` holds the API key **and** the connection details, so that a private endpoint never ends up
in a commit. Everything else — prompts and sampling parameters — lives in the JSON/YAML config
files described below.

### PDF dependencies

WeasyPrint needs native GTK/Pango libraries that `pip` does not install.

<details>
<summary><b>Windows (MSYS2)</b></summary>

```powershell
winget install MSYS2.MSYS2 --accept-package-agreements --accept-source-agreements
```

Open the **MSYS2 MinGW 64-bit** shell from the Start menu:

```bash
pacman -Syu --noconfirm
pacman -S --noconfirm mingw-w64-x86_64-gtk3 mingw-w64-x86_64-pango
```

Add `C:\msys64\mingw64\bin` to your PATH (permanently, via System Properties → Environment
Variables, or for the current session):

```powershell
$env:Path = "C:\msys64\mingw64\bin;$env:Path"
```

Restart your terminal/IDE, then verify:

```powershell
Get-ChildItem "C:\msys64\mingw64\bin" -Filter "*pango*.dll"
Get-ChildItem "C:\msys64\mingw64\bin" -Filter "*gobject*.dll"
```
</details>

<details>
<summary><b>macOS</b></summary>

```bash
brew install pango gdk-pixbuf libffi
```
</details>

<details>
<summary><b>Linux (Debian/Ubuntu)</b></summary>

```bash
sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libffi-dev
```
</details>

---

## Your data files

The pipeline reads three files from your data directory (`data/` by default). **They are not in
this repository** — `data/` and `outputs/` are gitignored, because they hold real personal data.
Create them yourself before the first run:

```
data/
├── cv.json              # your full career history (superset — the LLM selects from it)
├── personal_info.json   # identity + contact details (never sent to the LLM)
└── profile.txt          # free-text professional summary, used for the cover letter
```

### `cv.json`

A JSON Resume–style document. Top-level keys the prompts are written against:

```json
{
  "skills": [],
  "work": [],
  "education": [],
  "projects": [],
  "certificates": [],
  "publications": [],
  "languages": [],
  "interests": [],
  "references": [],
  "meta": {}
}
```

Keep it as a **superset** of everything you have ever done — each run picks the relevant subset
for the target job. The LLM is instructed not to invent anything that is not in this file.

### `personal_info.json`

```json
{
  "basics": {
    "name": "Jane Doe",
    "email": "jane@example.com",
    "phone": "+49 151 00000000",
    "location": { "city": "Berlin", "country": "Germany" },
    "profiles": []
  }
}
```

Every string in this file is what gets scrubbed from the job description, and `name`, `email`,
`phone` and `location` are what get re-injected into the generated CV header.

### `profile.txt`

A few paragraphs in your own voice about who you are professionally. Passed to the cover-letter
prompt as `candidate_profile`.

---

## Configuration

Four layers, each with a distinct job:

| File | Owns | In git |
| --- | --- | --- |
| `.env` | `LLM_API_KEY`, endpoint and model (`LLM_HOST`, `LLM_BASE_PATH`, `LLM_ENDPOINT`, `LLM_MODEL`) | no |
| `llm_config.json` | per-use-case sampling parameters | yes |
| `llm_prompts.yaml` | every system/user prompt, per task and language | yes |
| `USER_CONFIG.json` | language, location source, data/output directories | yes |

### `llm_config.json`

Connection details are read from `.env` and deliberately left empty here, so that a private
endpoint is never committed:

```json
{
  "model": "",
  "endpoint": "",
  "host": "",
  "base_path": "",

  "general_settings": {
    "temperature": 0.7,
    "max_tokens": 4000,
    "stream": false,
    "reasoning_effort": "medium"
  },
  "use_cases": {
    "job_extraction":           { "temperature": 0.3, "max_tokens": 3000 },
    "company_extraction":       { "max_tokens": 3000 },
    "cv_generation":            { "max_tokens": 2500 },
    "cover_letter_generation":  { "max_tokens": 2000 },
    "cv_scoring":               { "max_tokens": 1500 },
    "quick_test":               { "max_tokens": 100, "reasoning_effort": "low" }
  }
}
```

`LLM_HOST` + `LLM_BASE_PATH` are what the client actually dials (it uses
`http.client.HTTPSConnection` directly); `LLM_ENDPOINT` is informational. The environment
variables take precedence over the matching keys in `llm_config.json`, which stay empty. Each
`use_cases` entry overrides `general_settings` for that one call.

To point at a different provider, change `LLM_HOST`, `LLM_BASE_PATH` and `LLM_MODEL` in `.env` —
e.g. OpenAI is `LLM_HOST=api.openai.com`, `LLM_BASE_PATH=/v1/chat/completions`. Note that HTTPS is assumed, so
plain-HTTP local servers (Ollama, LM Studio) need a TLS proxy or a small change in
`llm_client.py`.

### `USER_CONFIG.json`

```json
{
  "language": "en",
  "location": "job",
  "paths": { "data": "data", "outputs": "outputs" }
}
```

- **`language`** — `en` or `de`; the default offered at startup.
- **`location`** — which location appears in the CV header:
  - `"job"` — the job's location (you are prompted if the advert does not state one),
  - `"user"` — your own location from `personal_info.json`.
- **`paths`** — where input is read from and output is written to.

### `llm_prompts.yaml`

All prompt text lives here, so you can retune the writing style without touching Python:

```yaml
languages: [en, de]

model_parse:
  job_info:               { system: ... }
  company_info:           { system: ... }
  score_cv_missing_skills:{ system: ..., user: ... }

make_CV:
  system: ...
  user_en: ...
  user_de: ...

make_coverletter:
  system: ...
  user_en: ...
  user_de: ...
```

**Adding a language** means adding a `user_<code>` key to *both* `make_CV` and `make_coverletter`,
not just extending the `languages` list.

---

## Running the pipeline

> Run from the repository root — `main.py` and `llm_client.py` open `llm_config.json` and
> `llm_prompts.yaml` by bare relative path.

```powershell
python src/pipeline/main.py
```

The session looks like this:

```
=== Job Application Pipeline with LLM Integration ===

Select language for CV and cover letter generation:
Enter your choice (e,d, Enter Last used:en):          <- e / d / Enter

Please paste the job description text (press Enter twice to finish):
...paste...                                            <- blank line ends input

Enter company name to search on Wikipedia: SAP SE      <- Enter to skip
```

From there it runs unattended (~1–2 minutes, depending on the endpoint), printing each stage.
It will only stop to ask again if `location` is `"job"` and the advert had no usable location.

Smoke-test connectivity first if you like — this costs ~100 tokens:

```powershell
python src/pipeline/llm_test.py
```

---

## Output

Each run creates `outputs/<yymmdd-HHMM>_<company>/`:

```
outputs/260716-1430_SAP SE/
├── CV_Jane_Doe_Senior_Data_Scientist.pdf
├── CoverLetter_Jane_Doe_Senior_Data_Scientist.pdf
├── cv.md                     # generated CV, Markdown
├── coverletter.md            # generated cover letter, Markdown
├── job.yaml                  # extracted JobInfo + CompanyInfo
├── missing_skills.txt        # compatibility score, strengths, gaps
└── raw/
    ├── cv_raw.html           # intermediate HTML handed to WeasyPrint
    └── job_description.txt   # the scrubbed advert as the LLM saw it
```

`missing_skills.txt` is worth reading even when you are happy with the CV — it is the fastest
signal about what to add to `cv.json`, or which requirement to address explicitly in the cover
letter.

Logs go to `logs/application.log` (5 MB rotation, 5 backups) and to stdout.

---

## Extra scripts

| Script | Purpose |
| --- | --- |
| `src/pipeline/llm_test.py` | Minimal round-trip against your endpoint (~100 tokens). |
| `src/pipeline/md_to_pdf.py` | Standalone Markdown → PDF conversion via a Tkinter file picker, no LLM involved. See [Known limitations](#known-limitations). |

---

## Customising the documents

**Layout and typography** live in `src/styles/cv.css` and `src/styles/coverletter.css` — print
stylesheets with an `@page` A4 rule and EB Garamond loaded from `src/fonts/`. Editing these is the
safe way to restyle the output.

**The Markdown dialect** is a contract between the prompts and the parser in `generator.py`:

| Markup | Meaning |
| --- | --- |
| `# {personal_info}` | Placeholder the LLM emits; replaced locally with your name and contact line. |
| `<<contact_info>>` | Leads the contact line; rendered as a `div.contact-info` with `tel:`/`mailto:` links. |
| `#### Title \| Company \| Location` followed by `_dates_` | Rendered as a two-column `table.cv-entry`. |

If you change the output format in `llm_prompts.yaml`, change `make_html_cv()` /
`make_html_coverletter()` to match — and vice versa.

`_humanize_text()` normalises em dashes, curly quotes and non-breaking spaces out of every LLM
response, so the result reads less machine-generated. Keep it in the path if you add a generator.

---

## Project layout

```
.
├── llm_config.json          # endpoint, model, sampling parameters
├── llm_prompts.yaml         # all prompts, per task and language
├── USER_CONFIG.json         # language, location source, paths
├── requirements.txt
├── .env.example             # template -> copy to .env
├── data/                    # YOUR files, gitignored (see "Your data files")
├── outputs/                 # generated applications, gitignored
├── logs/                    # rotating application log, gitignored
└── src/
    ├── pipeline/
    │   ├── main.py          # entry point / orchestration
    │   ├── llm_client.py    # OpenAI-compatible HTTP client + schema-constrained parsing
    │   ├── generator.py     # CV & cover-letter generation, Markdown -> HTML -> PDF
    │   ├── models.py        # JobInfo, CompanyInfo, CVScoreResponse (Pydantic)
    │   ├── llm_test.py      # connectivity smoke test
    │   └── md_to_pdf.py     # standalone converter
    ├── utils/
    │   ├── file_handler.py  # output folders, saving, file naming
    │   └── scraper.py       # Wikipedia + (disabled) company-site scraping
    ├── styles/              # cv.css, coverletter.css
    └── fonts/               # EB Garamond
```

There is no test suite, linter or build step.

---

## Troubleshooting

**`ValueError: LLM_API_KEY must be set in .env file`**
`.env` is missing, is not in the repo root, or has no `LLM_API_KEY=` line. The file is read from
the current working directory via `python-dotenv`.

**`FileNotFoundError: 'llm_config.json'` or `'llm_prompts.yaml'`**
You are not in the repository root. `cd` there and rerun `python src/pipeline/main.py`.

**`ConnectionError: LLM API request failed with status 401/404`**
Check `host` and `base_path` in `llm_config.json` (the client dials `https://<host><base_path>`)
and confirm the key. Reproduce outside the app:

```bash
curl -X POST https://<host><base_path> \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LLM_API_KEY" \
  -d '{"model":"<your-model>","messages":[{"role":"user","content":"hi"}]}'
```

**`Failed to parse LLM response` during extraction**
The endpoint most likely ignored `response_format: json_schema`, or the reply was truncated. Raise
the relevant `max_tokens` in `llm_config.json`, or switch to a model that supports structured
output.

**`cannot load library 'libgobject-2.0-0'` (or similar) on import of WeasyPrint**
The native PDF libraries are not on PATH — see [PDF dependencies](#pdf-dependencies). On Windows,
restart the terminal after editing PATH; VS Code needs a full restart, not just a new terminal.

**`FileNotFoundError: data/cv.json`**
Create your data files, or point `paths.data` in `USER_CONFIG.json` at wherever they live. The
pipeline warns and continues with degraded output rather than stopping.

**Wikipedia returns the wrong company**
Type the exact article title (e.g. `SAP SE`, not `SAP`). Press Enter at the prompt to skip the
lookup entirely and generate without company context.

---

## Known limitations

Honest list of the sharp edges, all reproducible in the current code:

- **Company-website scraping is disabled.** `_official_site_scraping()` and
  `CompanyWebsiteScraper` are implemented but the call site in `main.py` is commented out; only
  Wikipedia is used. Re-enabling it currently raises `NameError` because the robots.txt check in
  `src/utils/scraper.py` references an undefined `ROBOTS_USER_AGENT`.
- **`md_to_pdf.py` always exits with "No file selected"** — its `select_file()` never returns the
  path it picked.
- **The language preference is not persisted.** `_select_language()` rewrites `USER_CONFIG.json`
  without first updating the in-memory `language` key, so the file is written back unchanged.
- **`model_parser()` hardcodes `use_case="data_extraction"`**, a key that does not exist in
  `llm_config.json`. All schema extraction therefore falls back to `general_settings`, and the
  `use_case` argument callers pass in is ignored.
- **HTTPS only.** The client uses `HTTPSConnection` unconditionally, so plain-HTTP local endpoints
  will not work as-is.
- **`openai` is in `requirements.txt` but unused** — the client is hand-rolled on `http.client`.

---

## Privacy

- Your name, contact details and location are **never** included in any LLM request; they are
  merged into the documents locally after generation.
- The job advert *is* sent to the LLM, scrubbed of your personal strings first. The exact text
  that was sent is saved to `raw/job_description.txt` so you can verify this.
- `cv.json` and `profile.txt` **are** sent to the LLM — that is what tailoring requires. Choose an
  endpoint you trust accordingly, and consider a self-hosted model if that matters to you.
- `data/`, `outputs/`, `logs/` and `.env` are gitignored. Check before you push anyway.

---

## Contributing

Issues and pull requests are welcome.

```bash
git checkout -b feature/your-feature
# ...
git commit -m "FEAT your feature"
git push origin feature/your-feature
```

If you touch the CV format, remember it spans three files: the prompt in `llm_prompts.yaml`, the
parser in `generator.py`, and the stylesheet in `src/styles/`.

Never commit anything from `data/` or `outputs/`.

---

## License

MIT — see [LICENSE](LICENSE).
