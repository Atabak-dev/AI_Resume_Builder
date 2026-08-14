# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```powershell
# Setup (Python 3.12+)
python -m venv .venv; .\.venv\Scripts\activate
pip install -r requirements.txt

# Run the full pipeline — MUST be run from the repo root (see "Working directory" below)
python src/pipeline/main.py

# Smoke-test LLM connectivity only (uses the "quick_test" use case, ~100 tokens)
python src/pipeline/llm_test.py

# Standalone Markdown -> PDF conversion via a Tkinter file picker (no LLM needed)
python src/pipeline/md_to_pdf.py
```

There is no test suite, linter, or build step. `test.ipynb` is gitignored scratch.

PDF output requires WeasyPrint's native deps. On Windows that means MSYS2 with
`mingw-w64-x86_64-gtk3` and `mingw-w64-x86_64-pango`, and `C:\msys64\mingw64\bin` on PATH
(full instructions in [README.md](README.md)).

## Working directory

`main.py` and `llm_client.py` open `llm_config.json` and `llm_prompts.yaml` by **bare relative
path**, so the process CWD must be the repo root. Everything else (`FileHandler`, CSS, fonts,
`USER_CONFIG.json`) resolves paths relative to `__file__` instead. Don't "fix" one style into
the other without checking both call sites.

Imports are equally mixed: [main.py:8](src/pipeline/main.py#L8) does a flat
`from llm_client import ...` (works only because Python puts the script's own directory on
`sys.path`), while everything else uses `from src.pipeline...` after
[main.py:15-16](src/pipeline/main.py#L15-L16) inserts the repo root. New modules should use the
`src.` form.

## Architecture

Single-run interactive CLI. One invocation = one job application. Flow in
[main.py](src/pipeline/main.py):

1. **Input** — job description pasted into stdin (terminated by a blank line). Nothing is fetched
   from job boards. The company name is read out of the job description by the job-extraction call
   (step 3); the user confirms or corrects it before research starts (step 3.5), and is prompted to
   type it manually only when extraction comes back empty.
2. **Scrub** — `_remove_personal_info()` strips every leaf string from `personal_info.json` out of
   the job description before it reaches the LLM. This is the privacy contract of the project:
   **the candidate's identity never goes to the LLM.** Name/email/phone/location are re-injected
   locally into the generated Markdown afterwards.

   The contract binds anything that sends generated text *back* to the LLM. `make_cv()` stashes
   the pre-injection Markdown on `Generator_Handler.cv_markdown_raw`, and `test_cv()` must be
   given that, never `make_cv()`'s return value — the latter carries the contact block. Any new
   round-trip (re-scoring, revision passes) has the same obligation.

   The contract now also covers the company-research tool calls (step 3.5): `web_search` and
   `fetch_page` are hard-blocked (a `logger.critical` line plus an on-screen warning) if the
   query/URL contains a personal-info value at least 3 characters long, and every fetched page is
   scrubbed the same way before it re-enters the LLM. Both live in
   `src/utils/privacy.py:PersonalInfoScrubber` — `scrub()` for inbound text, `find_personal_info()`
   for the outbound guard. `min_length` (0 for the job description, 3 for tool traffic) exists
   because a `personal_info.json` leaf like a house number or a 2-letter country code would
   otherwise shred unrelated company prose.
3. **Extract** — `LLM_Handeler.model_parser()` sends a Pydantic-derived JSON schema plus context
   and returns a populated model (`JobInfo`, `CompanyInfo`, `CVScoreResponse`).
3.5. **Research** — `main._research_company()` gathers company background via
   `LLM_Handeler.run_tool_loop()`, which lets the model call `web_search`, `fetch_page` and
   `wikipedia_page` (`src/pipeline/tools.py:ResearchToolbox`) until it has enough to describe the
   company. Every new host is approved once by the user via
   `src/pipeline/tools.py:HostApprovalGate` before any page on it is fetched, and every URL actually
   fetched is printed as it happens (even on an already-approved host) and again in a summary before
   extraction — the same list is saved on `CompanyInfo.sources` and therefore in `job.yaml`. If the
   endpoint rejects `tools` (`ToolsUnsupportedError`), the loop falls back to a plain Wikipedia
   lookup plus the (now host-gated) `_official_site_scraping()` link-navigation flow.
   `research.enabled: false` in `USER_CONFIG.json` skips straight to the plain Wikipedia lookup.
4. **Generate** — `Generator_Handler` produces CV and cover-letter Markdown, then HTML, then PDF.
5. **Score** — `test_cv()` grades the generated CV against the job description and writes
   `missing_skills.txt`. Alongside the LLM's `compatibility_score`, `test_cv()` also runs
   `src/utils/keywords.py:KeywordMatcher` locally (no LLM call) to compute a deterministic
   keyword-match percentage plus matched/missing keyword lists, stored on `CVScoreResponse` via
   `SkipJsonSchema` fields so they never reach the LLM's response schema.

### Configuration layers

| File | Owns |
| --- | --- |
| `.env` | `LLM_API_KEY` + connection details `LLM_MODEL`/`LLM_HOST`/`LLM_BASE_PATH`/`LLM_ENDPOINT`, plus `SEARCH_PROVIDER` (supports a comma-separated order and an `only:` pin — see `.env.example`) and the matching `BRAVE_API_KEY`/`TAVILY_API_KEY`/`SERPER_API_KEY` (gitignored) |
| `llm_config.json` | Per-use-case sampling params. Its `model`/`endpoint`/`host`/`base_path` keys are committed empty — the env vars above override them, so no private endpoint reaches git |
| `llm_prompts.yaml` | Every system/user prompt, keyed by task and language, including `company_research.system` for the tool-calling loop |
| `USER_CONFIG.json` | `language` (`en`/`de`), `location` (`job`/`user`), `contact_fields.*` (booleans: `location`/`phone`/`email`/`linkedin` — which contact-line items to render on the CV), data/output dirs, plus `scraping.*` (timeout/delay/retries) and `research.*` (`enabled`, `max_iterations`, `max_fetches`, `max_searches`, `max_page_chars`, `search_results`) |
| `allowed_domains.txt` | Hosts pre-approved for company-research tool calls, one per line (`#` comments). Merged into `HostApprovalGate.AUTO_ALLOW` at the start of each run via `HostApprovalGate.load_domains_file()`, so listed hosts never trigger the interactive approval prompt |

`llm_config.json` `use_cases` entries (`cv_generation`, `cover_letter_generation`,
`job_extraction`, `cv_scoring`, …) override `general_settings` per call; `create_completion()`
picks them up from its `use_case` argument. Note that `model_parser()` hardcodes
`use_case="data_extraction"` ([llm_client.py:221](src/pipeline/llm_client.py#L221)) — that key
doesn't exist in `llm_config.json`, so all schema extraction silently falls back to
`general_settings` and the `use_case` parameter passed by callers is ignored.

`llm_prompts.yaml` structure matters: `model_parse.<task>.system` is looked up by the `prompt`
argument to `model_parser()`; generation prompts use `make_CV.user_<lang>` /
`make_coverletter.user_<lang>`, so **adding a language means adding a `user_<code>` key to every
generation block**, not just to the `languages` list.

### LLM client

[llm_client.py](src/pipeline/llm_client.py) talks to an OpenAI-compatible endpoint over raw
`http.client.HTTPSConnection` — the `openai` package in requirements.txt is not used. Structured
output goes through `response_format: json_schema` built from `Model.get_schema()`. Each model
implements `get_schema()` / `set_from_json()`; `JobInfo.get_schema()` deliberately deletes the
nested `company` property so company extraction stays a separate LLM call. The flat
`company_name` / `company_common_name` fields on `JobInfo` are exempt from that deletion on
purpose — they are how the hiring company is identified from the advert without a second call.
`get_schema()` also pops `$defs`, which only exists because the (still declared) `company` field
references `CompanyInfo`; don't remove that field without adjusting the pop.

`create_completion()` also accepts `tools`/`tool_choice` (OpenAI function-calling format) and
forces `stream=False` whenever `tools` is set — there is no SSE parser in this client.
`run_tool_loop(messages, toolbox, use_case, max_iterations=6)` drives the multi-turn loop: it calls
the endpoint with `toolbox.schemas`, dispatches any `tool_calls` through `toolbox.dispatch()`, and
feeds `role: "tool"` results back until the model stops calling tools or `max_iterations` is hit (at
which point one final call without `tools` asks for a summary). If the endpoint rejects a `tools`
request outright (400/404/422 with a tool/function-shaped error body), `create_completion()` raises
`ToolsUnsupportedError` and sets `self.supports_tools = False` for the rest of the process; callers
should catch that on the first iteration and fall back to a non-tool-calling path. **Tool calling
and `response_format: json_schema` are never sent in the same request** — `_research_company()` in
main.py deliberately runs the tool loop and `model_parser()`'s schema extraction as two separate
calls, because many OpenAI-compatible servers reject or mishandle that combination.

`model_parser()` used to hardcode `use_case="data_extraction"` regardless of the `use_case`
argument callers passed in; it now forwards the caller's `use_case` so `job_extraction` /
`company_extraction` / `cv_scoring` actually get their configured sampling parameters instead of
silently falling back to `general_settings`.

### Markdown → HTML → PDF

`Generator_Handler` hand-rolls a line-by-line Markdown parser rather than using a library, because
the CV format is a fixed dialect the prompts are written against:

- `# {personal_info}` — literal placeholder the LLM is told to emit; replaced locally with the
  real name and contact line.
- `<<contact_info>>` (`self.CONTACT_MARKER`) — leads the contact line, rendered as
  `<div class="contact-info">` with `tel:`/`mailto:` links.
- `#### Title | Company | Location` followed by `_dates_` — becomes a two-column
  `<table class="cv-entry">`.

Changing the prompts' output format therefore requires matching changes in `make_html_cv()`, and
vice versa. Styling lives in [src/styles/cv.css](src/styles/cv.css) and
[coverletter.css](src/styles/coverletter.css) (print CSS: `@page` A4, EB Garamond loaded from
`src/fonts/` via a path relative to `base_url` in `make_pdf()`).

`_humanize_text()` normalises em dashes, curly quotes and NBSP out of every LLM response — it
exists to make output look less machine-generated; keep it in the path when adding generators.

## Data and outputs

**`data/` and `outputs/` hold the user's real personal data and are off-limits.**
`.claude/settings.json` (local-only, gitignored) denies `Read` on both (and `Edit` on `data/**`).
Don't work around it (no `cat`, no `python -c` dumps) — use
[data_example/](data_example/) when you need to know the shape of a file, and ask the user to
paste redacted snippets if you need more.

`data/` and `outputs/` are gitignored (real personal data). [data_example/](data_example/) is the
committed, fictional reference: `cv_sample.json` for the `cv.json` shape (`skills`, `work`,
`education`, `projects`, `certificates`, `publications`, `languages`, `interests`, `references`,
`meta`), `personal_info_sample.json` for `personal_info.json` (a `basics` object — `name`,
`email`, `phone`, `location`, `profiles`), and `profile_sample.txt` for `profile.txt`.

Each run creates `outputs/<yymmdd-HHMM>_<company>/` containing `cv.md`, `coverletter.md`,
`job.yaml`, `missing_skills.txt`, the two PDFs, and `raw/` (`cv_raw.html`, `job_description.txt`).
`<company>` is `JobInfo.company_common_name`, which is LLM output derived from an untrusted advert,
so `make_output_folder()` strips path-illegal characters and caps it at 60 chars; with no company
the folder is just the timestamp.
Logs go to `logs/application.log` with 5 MB rotation, plus stdout.

## Web research (company background)

`main._research_company()` (called from the company block in `main()`) replaces the old
Wikipedia-only lookup. Building blocks:

- [src/utils/search.py](src/utils/search.py) — `SearchProvider` ABC with keyless
  `DuckDuckGoProvider` (default, scrapes `html.duckduckgo.com`) and `BingProvider` (fallback,
  scrapes `www.bing.com/search`, unwraps its `/ck/a?...&u=a1<base64url>` redirect links) plus keyed
  `BraveProvider`/`TavilyProvider`/`SerperProvider`. Every provider sets `last_status`
  (`"ok"`/`"empty"`/`"blocked"`/`"error"`) and latches `blocked = True` once it serves a
  bot-challenge page, so `ChainedProvider` can fall through to the next backend instead of treating
  a challenge as "no results". `get_search_provider()` resolves `SEARCH_PROVIDER` (or the `name`
  arg) into an ordered chain — default is any keyed provider whose API key is set, then
  `duckduckgo`, then `bing`; see `.env.example` for the `only:`/comma-list syntax.
- [src/pipeline/tools.py](src/pipeline/tools.py) — `ResearchToolbox` implements the three LLM tools
  (`web_search`, `fetch_page`, `wikipedia_page`); `HostApprovalGate` gates `fetch_page`/
  `wikipedia_page` per-host (any `*.wikipedia.org` edition, the DDG/Bing search endpoints, and
  anything in the repo-root `allowed_domains.txt` are auto-allowed) and prints a trace line for
  every fetch regardless of whether the host was already approved. `web_search` also has its own
  budget (`max_searches`, default 6) separate from `fetch_page`'s `max_fetches`, and an empty
  result carries a `hint` field telling the model whether to reword once or stop searching
  entirely — this is what stops the tool loop from burning all its iterations rewording a query
  against a fully blocked search backend.
- [src/pipeline/llm_client.py](src/pipeline/llm_client.py) — `run_tool_loop()` drives the
  tool-calling conversation (see the LLM client section above for the tools/`response_format`
  separation rule).
- [src/utils/scraper.py](src/utils/scraper.py) — `CompanyWebsiteScraper.find_official_website()`
  ranks real search results (rejecting LinkedIn/Glassdoor/Crunchbase/etc. by exact-or-suffix host
  match, not substring) using `significant_tokens()` (legal-form/generic-corporate stopwords
  stripped) matched against `_registrable_label()` (aware of two-part suffixes like `co.uk`).
  `WikipediaScraper.resolve_article()` returns a `WikiArticle(title, language, url)` or `None` —
  never a guess: it prefers finding the article's URL via the search provider (so
  `title_matches_company()`'s anchor-token check can verify it's really the same company before
  accepting), hops to the configured language edition via `langlinks()` when one exists, and falls
  back to the MediaWiki `opensearch` API filtered the same way. `wikipedia-api` 0.15.0 does expose
  `.search()`/`.langlinks()` despite older assumptions to the contrary.

New tool code adding a network call must go through
`src/utils/privacy.py:PersonalInfoScrubber` on both ends — see the privacy-contract note in
"Architecture" above. `_research_company()` also checks the company name itself before any research
call is made, since every outbound query (search, Wikipedia, site scraping) is seeded from it.

Adding a search backend: implement `.search(query, max_results) -> list[SearchResult]`, set
`last_status`/`blocked` like the existing providers, then register it in `_KEYED_PROVIDERS` (with an
env var) or `_KEYLESS_PROVIDERS`/`_DEFAULT_KEYLESS_ORDER` in `search.py`, and document the env var
in `.env.example`.

## Known rough edges

- `select_file()` in [md_to_pdf.py:16](src/pipeline/md_to_pdf.py#L16) never returns the path it
  selected, so the standalone converter always exits with "No file selected".
- `_select_language()` writes `USER_CONFIG.json` back out but never updates the in-memory
  `language` key first, so the new preference isn't actually persisted.
- DuckDuckGo's HTML endpoint currently serves a bot-challenge page (HTTP 202 with an
  `anomaly-modal` CAPTCHA) to most datacenter/residential IPs rather than search results.
  `DuckDuckGoProvider.search()` detects this (`_CHALLENGE_MARKERS`), latches `.blocked = True` for
  the rest of the run, and logs/prints it loudly instead of the misleading old "markup change or
  rate limiting" message; the chain then falls through to `BingProvider`. Both keyless backends are
  unauthenticated HTML scraping against their operators' terms and can break again without notice —
  set `SEARCH_PROVIDER=brave` (or tavily/serper) with an API key for reliable use.
- The README is largely aspirational marketing copy generated alongside the project — trust the
  code over it, except for the MSYS2/WeasyPrint install steps.
