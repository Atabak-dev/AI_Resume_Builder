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
- [Web research](#web-research)
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
paste job advert ─► scrub personal info ─► LLM: extract JobInfo ─────────────────────┐
                                           (incl. the company name)                  │
                                                    │                                ▼
                                                    ▼                        LLM: write CV ─► Markdown ─► HTML ─► PDF
                                          confirm company name                       │
                                                    │                                ├──► LLM: score CV ─► missing_skills.txt
                                                    ▼                                │
                                    LLM tool loop: web_search / fetch_page /         └──► LLM: write cover letter ─► Markdown ─► HTML ─► PDF
                                    wikipedia_page (host approval per site)
                                                    │
                                                    ▼
                                          LLM: extract CompanyInfo
```

1. **Input** — you paste the job description into stdin (blank line to finish). Nothing is fetched
   from job boards.
2. **Scrub** — `_remove_personal_info()` removes every leaf string of `personal_info.json` from
   the advert text, case-insensitively, including `word_with_underscores` variants. The same check
   guards every web-research tool call in step 3.5 (see [Web research](#web-research)).
3. **Extract** — a JSON-schema-constrained call fills the Pydantic models `JobInfo` and
   `CompanyInfo`. `JobInfo` carries both the company's formal name (`company_name`, e.g.
   `Carl Zeiss AG`) and its common short name (`company_common_name`, e.g. `Zeiss`); the short name
   is what you confirm before research starts, and what drives the output folder.
3.5. **Research** — you confirm or correct the detected company name, then the LLM researches it
   using `web_search`, `fetch_page` and `wikipedia_page` tools, asking your approval before it
   fetches any new website. See [Web research](#web-research).
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

The pipeline reads three files from your data directory (`data/` by default). **Your copies are
not in this repository** — `data/` and `outputs/` are gitignored, because they hold real personal
data.

```
data/
├── cv.json              # your full career history (superset — the LLM selects from it)
├── personal_info.json   # identity + contact details (never sent to the LLM)
└── profile.txt          # free-text professional summary, used for the cover letter
```

Fictional samples of all three live in [`data_example/`](data_example/). Copy them across and
edit in your own details:

```bash
mkdir -p data
cp data_example/cv_sample.json            data/cv.json
cp data_example/personal_info_sample.json data/personal_info.json
cp data_example/profile_sample.txt        data/profile.txt
```

```powershell
mkdir data
copy data_example\cv_sample.json            data\cv.json
copy data_example\personal_info_sample.json data\personal_info.json
copy data_example\profile_sample.txt        data\profile.txt
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
    "company_research":         { "temperature": 0.2, "max_tokens": 3000 },
    "cv_generation":            { "max_tokens": 2500 },
    "cover_letter_generation":  { "max_tokens": 2000 },
    "cv_scoring":               { "max_tokens": 1500 },
    "quick_test":               { "max_tokens": 100, "reasoning_effort": "low" }
  }
}
```

`company_research` is used by the tool-calling loop that gathers company background (see
[Web research](#web-research)) — a separate, lower-temperature use case from `company_extraction`,
which only turns the gathered text into the structured `CompanyInfo` fields.

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
  "paths": { "data": "data", "outputs": "outputs" },
  "scraping": { "company_website_timeout": 30, "request_delay": 1.0, "max_retries": 3 },
  "research": {
    "enabled": true,
    "max_iterations": 6,
    "max_fetches": 8,
    "max_page_chars": 12000,
    "search_results": 5
  }
}
```

- **`language`** — `en` or `de`; the default offered at startup.
- **`location`** — which location appears in the CV header:
  - `"job"` — the job's location (you are prompted if the advert does not state one),
  - `"user"` — your own location from `personal_info.json`.
- **`paths`** — where input is read from and output is written to.
- **`scraping`** — timeout/pacing for fetching the company's own website.
- **`research`** — caps on the company-research tool loop; see [Web research](#web-research).
  Set `research.enabled` to `false` to skip straight to a plain Wikipedia lookup (e.g. offline).

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

## Web research

After the job description is extracted, the pipeline researches the hiring company by giving the
LLM three tools it can call itself: `web_search`, `fetch_page`, and `wikipedia_page`.

**Search backend** — real API providers only; there is no keyless default. `SEARCH_PROVIDER` in
`.env` selects it. Left unset (or `auto`), the pipeline tries every provider below that has an API
key set, Brave first. A single name prioritises that provider ahead of the others; a comma-separated
list sets an exact order; an `only:` prefix (e.g. `only:brave`) pins one provider with no fallback.

| Value | Signup | Notes |
| --- | --- | --- |
| `brave` | free tier, 2000 queries/month | Set `BRAVE_API_KEY`. Recommended for regular use. |
| `tavily` / `serper` | free tier | Set `TAVILY_API_KEY` / `SERPER_API_KEY`. |

**No API key configured?** The pipeline will not silently do nothing — it asks you to paste the
company's official website directly, then researches from that page and its linked "about" pages
instead of searching for it. Set `research.manual_website_fallback: false` in `USER_CONFIG.json` to
disable that prompt for unattended runs. Earlier versions of this tool scraped DuckDuckGo's and
Bing's HTML search pages as a free, keyless fallback; both were removed after Bing was found to
serve well-formed, plausible-looking result pages for a completely unrelated query (see
`CLAUDE.md`'s "Known rough edges" for the investigation) — silently wrong research is worse than
none, so a real API key or manual entry are the only paths now.

**You stay in control of what gets accessed.** Before the assistant fetches a page on a host it
hasn't touched yet this run, you are asked once:

```
The assistant wants to access a new website:
  Host  : acme.com
  URL   : https://acme.com/about
  Reason: Likely contains mission and values
Allow all pages on this host for this run? [y/N]:
```

Approving covers the whole host for the rest of the run — you won't be asked again for
`acme.com/careers`, but a different host like `careers.acme.com` is a separate approval. Every page
actually fetched is also printed as it happens, even on an already-approved host, and again in a
summary before extraction:

```
  -> fetching https://acme.com/about  (host acme.com already approved)
     ok, 6120 chars

Accessed 3 page(s) for Acme GmbH:
  https://en.wikipedia.org/wiki/Acme_GmbH
  https://acme.com/
  https://acme.com/about
```

That URL list is saved to `CompanyInfo.sources` and ends up in `job.yaml`, so every run has a
permanent record of what was actually read. Research is scoped to the company's own website and
Wikipedia only — the system prompt instructs the model not to fetch review sites, news, or social
media, and never to search for a person.

**Privacy** applies to this traffic too: a `web_search`/`fetch_page` call is refused outright if the
query or URL contains anything from `personal_info.json`, and every fetched page is scrubbed of your
personal details before it is shown to the LLM (see [Privacy](#privacy)).

**If your endpoint doesn't support tool calling**, the pipeline falls back automatically to a plain
Wikipedia lookup plus a single-shot "pick likely about/mission pages" call — same host-approval
prompts, just no back-and-forth tool loop. Check support in advance for ~200 tokens:

```powershell
python src/pipeline/llm_test.py --tools
```

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

Extracting work position ...
Work position extraction successful
Detected company: SAP                                  <- read from the advert
Press Enter to research 'SAP', or type the correct company name:   <- Enter / type a correction
Researching company (web search + Wikipedia) ...
```

From there it mostly runs unattended (~1–3 minutes, depending on the endpoint), printing each
stage, but it will pause to ask your approval the first time it wants to fetch a page on a new
website (see [Web research](#web-research)) — that's expected, not a hang. It also stops to ask
again if the advert names no company (anonymised posting, recruiting agency), or if `location` is
`"job"` and the advert had no usable location. The company fallback looks like this, and pressing
Enter there skips research entirely:

```
Company name could not be detected. Enter it manually (or press Enter to skip):
```

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
├── job.yaml                  # extracted JobInfo + CompanyInfo (incl. CompanyInfo.sources)
├── missing_skills.txt        # compatibility score, strengths, gaps
└── raw/
    ├── cv_raw.html           # intermediate HTML handed to WeasyPrint
    └── job_description.txt   # the scrubbed advert as the LLM saw it
```

`missing_skills.txt` is worth reading even when you are happy with the CV — it is the fastest
signal about what to add to `cv.json`, or which requirement to address explicitly in the cover
letter. `job.yaml`'s `company.sources` list is every URL the research step actually fetched for
that run — a permanent record you can check against what ended up in the cover letter.

Logs go to `logs/application.log` (5 MB rotation, 5 backups) and to stdout.

---

## Extra scripts

| Script | Purpose |
| --- | --- |
| `src/pipeline/llm_test.py` | Minimal round-trip against your endpoint (~100 tokens). Add `--tools` to check tool-calling support instead. |
| `src/pipeline/md_to_pdf.py` | Standalone Markdown → PDF conversion via a Tkinter file picker, no LLM involved. See [Known limitations](#known-limitations). |
| `python -m src.utils.search "<query>"` | Smoke test of the configured search backend (no LLM). Requires a `BRAVE_API_KEY`/`TAVILY_API_KEY`/`SERPER_API_KEY` in `.env`. Add `--provider <name>` to test one backend specifically. |
| `python -m src.utils.scraper --wiki "<company>"` | Resolves a company name to a Wikipedia article via opensearch only (no guessing — prints `None` if nothing confidently matches) and fetches it. |
| `python -m src.utils.scraper --wiki-search "<company>"` | Same, but resolves via the search backend first — this is the path that finds the right article/edition when opensearch alone can't. |
| `python -m src.utils.scraper --match "<company>" "<title>"` | Prints whether a Wikipedia article title is confidently the same company. |
| `python -m src.utils.scraper --robots <url>` | Checks whether a URL is allowed by that host's robots.txt. |
| `python -m src.pipeline.tools "<company>"` | Exercises `web_search` + `wikipedia_page` directly, no LLM involved. |

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
├── USER_CONFIG.json         # language, location source, paths, scraping/research limits
├── requirements.txt
├── .env.example             # template -> copy to .env
├── data_example/            # fictional samples of the three input files
├── data/                    # YOUR files, gitignored (see "Your data files")
├── outputs/                 # generated applications, gitignored
├── logs/                    # rotating application log, gitignored
└── src/
    ├── pipeline/
    │   ├── main.py          # entry point / orchestration
    │   ├── llm_client.py    # OpenAI-compatible HTTP client + schema-constrained parsing + tool-calling loop
    │   ├── tools.py          # ResearchToolbox (web_search/fetch_page/wikipedia_page) + HostApprovalGate
    │   ├── generator.py     # CV & cover-letter generation, Markdown -> HTML -> PDF
    │   ├── models.py        # JobInfo, CompanyInfo, CVScoreResponse (Pydantic)
    │   ├── llm_test.py      # connectivity smoke test (+ --tools support probe)
    │   └── md_to_pdf.py     # standalone converter
    ├── utils/
    │   ├── file_handler.py  # output folders, saving, file naming
    │   ├── privacy.py       # PersonalInfoScrubber - the privacy contract, in one place
    │   ├── search.py        # SearchProvider backends (Brave/Tavily/Serper, keyed only) + ChainedProvider
    │   └── scraper.py       # Wikipedia lookup + company-site scraping
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
Copy the samples out of [`data_example/`](data_example/), or point `paths.data` in
`USER_CONFIG.json` at wherever your files live. The
pipeline warns and continues with degraded output rather than stopping.

**Wikipedia returns the wrong company**
`wikipedia_page` verifies every candidate article against the company name (its distinguishing word
must actually appear in the title) before accepting it — a company on Wikipedia with a similar but
different name (e.g. "VNR Group" vs. "VR Group") is rejected rather than substituted, and the result
is `status: "not_found"` instead. The run still completes either way; `CompanyInfo` is just filled
from whatever other text was gathered (the official website, mainly). If it still resolves to the
wrong company, correct the name at the confirmation prompt (`Press Enter to research 'X', or type
the correct company name:`) before research starts.

**The assistant keeps asking to approve the same kind of site**
Each *host* is approved once per run, not once ever — a fresh `python src/pipeline/main.py`
invocation starts with a clean approval list by design (see [Web research](#web-research)). This is
intentional: persisting approvals across runs was deliberately left out so you keep reviewing what
gets accessed.

**Research seems to return very little, or I keep getting asked for the website by hand**
Check whether `BRAVE_API_KEY`/`TAVILY_API_KEY`/`SERPER_API_KEY` is set in `.env` — with none set,
`get_search_provider()` returns nothing and the pipeline always falls back to the manual-website
prompt (see [Web research](#web-research)). With a key set, also check
`python src/pipeline/llm_test.py --tools` — if the endpoint doesn't support tool calling, the
fallback path (Wikipedia + one navigation call) gathers less than the full tool loop. Repeated
`status: "empty"` tool results in `logs/application.log` usually mean the free-tier quota was hit
(look for a `blocked` status and an HTTP 429 warning).

---

## Known limitations

Honest list of the sharp edges, all reproducible in the current code:

- **`md_to_pdf.py` always exits with "No file selected"** — its `select_file()` never returns the
  path it picked.
- **The language preference is not persisted.** `_select_language()` rewrites `USER_CONFIG.json`
  without first updating the in-memory `language` key, so the file is written back unchanged.
- **HTTPS only.** The client uses `HTTPSConnection` unconditionally, so plain-HTTP local endpoints
  will not work as-is.
- **`openai` is in `requirements.txt` but unused** — the client is hand-rolled on `http.client`.
- **There is no keyless search fallback.** Earlier versions scraped DuckDuckGo's and Bing's HTML
  search pages; both were removed after Bing was found to serve well-formed but completely wrong
  results for a query with no detectable sign anything was off (see `CLAUDE.md`'s "Known rough
  edges"). Without `BRAVE_API_KEY`/`TAVILY_API_KEY`/`SERPER_API_KEY` set, the pipeline asks you to
  paste the company's website by hand instead of guessing.
- **Host approvals reset every run.** There is no persisted allowlist across invocations, on purpose
  — see the Troubleshooting entry above.

---

## Privacy

- Your name, contact details and location are **never** included in any LLM request; they are
  merged into the documents locally after generation.
- That holds for the scoring round-trip too. `make_cv()` keeps the LLM's own output on
  `Generator_Handler.cv_markdown_raw` before the contact block is merged in, and `test_cv()` is
  given that version — so the CV that goes back to the endpoint for grading is anonymous.
- The job advert *is* sent to the LLM, scrubbed of your personal strings first. The exact text
  that was sent is saved to `raw/job_description.txt` so you can verify this.
- `cv.json` and `profile.txt` **are** sent to the LLM — that is what tailoring requires. Choose an
  endpoint you trust accordingly, and consider a self-hosted model if that matters to you.
- The same scrub applies to company research: any `web_search`/`fetch_page` call is refused if the
  query or URL contains your personal information, and every page fetched during research is
  scrubbed of it before the LLM sees the text. You still approve every new website by host before
  it's touched at all — see [Web research](#web-research).
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
