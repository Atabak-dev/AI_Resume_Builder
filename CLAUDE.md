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

- [src/utils/search.py](src/utils/search.py) — `SearchProvider` ABC, keyed-only:
  `BraveProvider`/`TavilyProvider`/`SerperProvider` (each needs `<PROVIDER>_API_KEY` in `.env`).
  There is no keyless default — see "Known rough edges" for why the DuckDuckGo/Bing scrapers were
  removed. Every provider sets `last_status` (`"ok"`/`"empty"`/`"blocked"`/`"error"`) and latches
  `blocked = True` on a 401/403 (bad key) or 429 (quota exhausted) so the rest of the run doesn't
  keep hitting a dead backend; `ChainedProvider` falls through to the next configured provider on
  any non-`ok` status. `get_search_provider()` resolves `SEARCH_PROVIDER` (or the `name` arg) into
  an ordered chain of whichever keyed providers have keys set, and returns `None` — not a provider
  — when none do; callers must handle that (see `main._research_company()`'s manual-website
  fallback below). See `.env.example` for the `only:`/comma-list syntax.
- [src/pipeline/tools.py](src/pipeline/tools.py) — `ResearchToolbox` implements the three LLM tools
  (`web_search`, `fetch_page`, `wikipedia_page`); `HostApprovalGate` gates `fetch_page`/
  `wikipedia_page` per-host (any `*.wikipedia.org` edition and anything in the repo-root
  `allowed_domains.txt` are auto-allowed; `HostApprovalGate.approve()` also lets a URL the user
  typed in by hand skip the prompt) and prints a trace line for every fetch regardless of whether
  the host was already approved. `web_search` returns `status: "unavailable"` immediately when
  `self.provider is None` (no key configured) instead of calling anything; otherwise it has its own
  budget (`max_searches`, default 6) separate from `fetch_page`'s `max_fetches`, and an empty result
  carries a `hint` field telling the model whether to reword once or stop searching entirely — this
  is what stops the tool loop from burning all its iterations rewording a query against a blocked
  search backend. `fetch_page` re-routes any `wikipedia.org` URL straight into `wikipedia_page`
  instead of scraping it as plain HTML, so the entity check below can't be bypassed by asking for a
  Wikipedia page through the wrong tool.
- [src/pipeline/llm_client.py](src/pipeline/llm_client.py) — `run_tool_loop()` drives the
  tool-calling conversation (see the LLM client section above for the tools/`response_format`
  separation rule).
- [src/utils/scraper.py](src/utils/scraper.py) — `CompanyWebsiteScraper.find_official_website()`
  ranks real search results (rejecting LinkedIn/Glassdoor/Crunchbase/etc. by exact-or-suffix host
  match, not substring) using `significant_tokens()` (legal-form/generic-corporate stopwords
  stripped) matched against `_registrable_label()` (aware of two-part suffixes like `co.uk`).
  `WikipediaScraper.resolve_article()` returns a `WikiArticle(title, language, url,
  official_website, verified)` or `None` — never a guess. Every candidate title (the exact-title
  probe, each search-engine hit, the `langlinks()` edition hop, each `opensearch` result) is routed
  through the single gate `_accept()`, which requires **both**:
  1. `title_matches_company()` — the anchor-token check that the title names the same company, and
  2. `classify_article()` — a tri-state check (`True`/`False`/`None` for "unverifiable") of whether
     the article is actually about an *organisation*. It reads the article's Wikidata item (`P31`
     "instance of", one `P279` "subclass of" hop if the direct value isn't in
     `_WIKIDATA_ORG_TYPES`), falling back to a category-title regex (`_ORG_CATEGORY_RE`) when the
     article has no wikibase item or Wikidata can't be reached. `True` or `False` are decisive;
     `None` (unverifiable) is treated as a rejection, never as a pass — see "Known rough edges" for
     why this existed as a gap (`en:Fuseki`, the Go opening, used to pass for `fuseki GmbH` on title
     matching alone).

  `classify_article()` also returns the article's official website when the entity check passes —
  `P856` from the same Wikidata claims, or `WikipediaScraper._extlink_website()` (the article's
  first external link that isn't a known aggregator and shares a token with the company name, via
  the same `_is_non_official()`/`_registrable_label()` helpers `find_official_website()` uses) when
  `P856` is absent. This is what lets a verified Wikipedia article seed the next scraping step
  instead of a fresh search (see below). `wikipedia-api` 0.15.0 does expose `.search()`/
  `.langlinks()` despite older assumptions to the contrary.

  `ResearchToolbox.wikipedia_page()` adds one more check on top of `resolve_article()`: the
  *resolved* article's title is cross-checked against the toolbox's real `company_name`/
  `legal_name` (`title_matches_company(..., min_coverage=0.5)`), not just against whatever title the
  model itself asked for — `resolve_article()` alone can't tell a self-consistent wrong request from
  a right one, since the model's own input *is* the `company_name` argument it receives. A failure
  here returns `status: "rejected"` (article found but not verifiably this company) as distinct from
  `status: "not_found"` (nothing matched at all); the system prompt tells the model not to retry
  either with a different title.

New tool code adding a network call must go through
`src/utils/privacy.py:PersonalInfoScrubber` on both ends — see the privacy-contract note in
"Architecture" above. `_research_company()` also checks the company name itself before any research
call is made, since every outbound query (search, Wikipedia, site scraping) is seeded from it. Its
first `web_search` query is seeded from the registered legal name and job location too (passed in
from `JobInfo`), not just the short common name — a bare short name collides with unrelated
products/projects (`Fuseki` vs. Apache Jena's SPARQL server `Fuseki`).

`_research_company()` ends its automatic phase (both the tool-calling branch and the
`ToolsUnsupportedError`/no-search-key fallbacks) with two idempotent helpers:
- `_ensure_wikipedia_attempted()` — if `toolbox.has_wikipedia_source` is still false and
  `research.manual_wikipedia_fallback` is on, offers a **one-time** prompt
  (`_prompt_manual_wikipedia()`) to paste the article URL by hand. Supplying it *is* the assertion
  that it's correct, so `ResearchToolbox.manual_wikipedia()` fetches it through `gate.approve()`
  with no entity check — same reasoning as a manually-typed website. A blank answer (most
  companies) is a normal outcome, not retried.
- `_ensure_website_source()` — if `toolbox.has_website_source` is still false (no *non*-Wikipedia
  page was ever fetched — the gap that used to let a Wikipedia-only run silently skip the website
  entirely), tries `toolbox.wikipedia_official_website` first, then `find_official_website()` via
  search, then `_prompt_manual_website()` as the last resort; whichever homepage is found feeds
  `_official_site_scraping()`, the same homepage-then-linked-pages flow used by the
  `ToolsUnsupportedError` fallback. `gate.approve()` is only called for the manually-typed case —
  an automatically-discovered homepage still goes through the normal interactive host-approval
  prompt.

Both prompts are controlled by `research.manual_website_fallback` / `research.manual_wikipedia_fallback`
in `USER_CONFIG.json` (default `true` for both). If research still yields no sources afterwards,
`_research_company()` returns `("", [])` and prints a warning rather than letting an empty
`CompanyInfo` get extracted from nothing — `main()` already skips the `company_extraction` call when
the context string is empty.

Adding a search backend: implement `.search(query, max_results) -> list[SearchResult]`, set
`last_status`/`blocked` like the existing providers (all inherit `_throttle()`/`_cached()` from the
`SearchProvider` ABC), then register it in `_KEYED_PROVIDERS` (name → (class, env var)) in
`search.py`, and document the env var in `.env.example`.

## Known rough edges

- `select_file()` in [md_to_pdf.py:16](src/pipeline/md_to_pdf.py#L16) never returns the path it
  selected, so the standalone converter always exits with "No file selected".
- `_select_language()` writes `USER_CONFIG.json` back out but never updates the in-memory
  `language` key first, so the new preference isn't actually persisted.
- Wikipedia article acceptance used to be pure title-string matching (`title_matches_company()`
  alone), which let a same-named article about something else entirely through — researching
  `fuseki GmbH` (no Wikipedia article) resolved to `en.wikipedia.org/wiki/Fuseki`, an opening
  strategy in the board game Go, because `significant_tokens("fuseki GmbH")` is the single token
  `["fuseki"]` and any one-token match short-circuited to a pass. Fixed by the entity check in
  `classify_article()` described in "Web research" above — a same-named article is now rejected
  unless Wikidata (or its categories) independently confirm it's an organisation.
- **Do not re-add a keyless DuckDuckGo/Bing scraper.** Both were implemented and removed after live
  testing. DuckDuckGo's HTML endpoint serves a bot-challenge page (HTTP 202, `anomaly-modal` CAPTCHA)
  to most datacenter/residential IPs instead of results — at least an honest, detectable failure.
  Bing was worse: probing it directly (`GET https://www.bing.com/search?q=...`) returned HTTP 200,
  a page title and searchbox correctly echoing the query, and 10 well-formed `li.b_algo` results
  with real titles/hosts/links — for a *completely unrelated* query. Three consecutive requests for
  `"fuseki GmbH"` returned Persian third-grade maths homework, then WhatsApp, then YouTube Help,
  each page footnoted "Some results have been removed". This is structurally indistinguishable from
  a genuine result page, so no parser or challenge-heuristic can catch it — it silently poisons the
  LLM's research context with plausible-looking wrong data, which is worse than returning nothing.
  Real search APIs (Brave/Tavily/Serper) are the only supported path now; without a key,
  `_research_company()` prompts the user for the company website by hand instead.
- The README is largely aspirational marketing copy generated alongside the project — trust the
  code over it, except for the MSYS2/WeasyPrint install steps.
