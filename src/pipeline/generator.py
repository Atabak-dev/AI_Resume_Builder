from typing import Dict, List, Optional, Any
import re
from datetime import datetime
import logging

import os
import yaml
import json

# Import JobInfo and CVScoreResponse from the correct module path
from src.pipeline.models import CVScoreResponse, CompanyInfo, JobInfo
from src.pipeline.llm_client import LLM_Handeler

# Configure logger
logger = logging.getLogger(__name__)



class Generator_Handler:
    """
    Handles tailored CV creation and editing using LLM.
    """
    
    def __init__(self, llm:LLM_Handeler, language, llm_config) -> None:
        """Initialize the CVGenerator.

        The generator relies on the LLM client which requires an API key.
        The API key is read from the ``LLM_API_KEY`` environment variable.
        If the variable is missing and `llm` is None, an informative ``EnvironmentError`` is raised.
        
        Args:
            llm: LLM client instance. If None, only PDF conversion methods are available.
            language: Language for generated CV and cover letter ('en' or 'de').
        """
        logger.info("Initializing Generator_Handler")
        
        # Only check for LLM_API_KEY if llm is not None
        if llm is not None:
            api_key = os.getenv("LLM_API_KEY")
            if not api_key:
                error_msg = "LLM_API_KEY environment variable not set"
                logger.error(error_msg)
                raise EnvironmentError(error_msg)
        
        # Initialise a single LLM client instance for reuse
        self.llm = llm
        self.language = language
        logger.info(f"Generator initialized with language: {language}")

        self.prompts = llm_config
        logger.debug("LLM configuration loaded")

        self.BOLD_RE = re.compile(r"\*\*(.*?)\*\*")
        self.ITALIC_RE = re.compile(r"[\*_](.*?)[\*_]")
        self.CODE_RE = re.compile(r"`(.*?)`")


        self.CONTACT_MARKER = "<<contact_info>>"
        
        # Load CV CSS from external file
        logger.info("Loading CV CSS styles")
        cv_css_path = os.path.join(os.path.dirname(__file__), '..', 'styles', 'cv.css')
        try:
            with open(cv_css_path, 'r', encoding='utf-8') as f:
                self.CV_CSS_STYLES = f.read()
            logger.info(f"CV CSS styles loaded successfully from {cv_css_path}")
        except FileNotFoundError as e:
            logger.error(f"CV CSS file not found: {e}")
            raise
        except Exception as e:
            logger.error(f"Error loading CV CSS: {e}")
            raise

        self.HTML_HEADER_CV = f"""<!DOCTYPE html>\
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
    {self.CV_CSS_STYLES}
</style>
</head>
<body>
"""

        self.HTML_FOOTER_CV = """\
</body>
</html>"""

        # HTML template for a professional cover letter. The styling mirrors the CV
        # but is slightly simplified to focus on the letter body and a clean header.
        # Load Cover Letter CSS from external file
        logger.info("Loading cover letter CSS styles")
        coverletter_css_path = os.path.join(os.path.dirname(__file__), '..', 'styles', 'coverletter.css')
        try:
            with open(coverletter_css_path, 'r', encoding='utf-8') as f:
                self.COVERLETTER_CSS_STYLES = f.read()
            logger.info(f"Cover letter CSS styles loaded successfully from {coverletter_css_path}")
        except FileNotFoundError as e:
            logger.error(f"Cover letter CSS file not found: {e}")
            raise
        except Exception as e:
            logger.error(f"Error loading cover letter CSS: {e}")
            raise

        self.HTML_HEADER_COVERLETTR = f"""<!DOCTYPE html>\
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cover Letter</title>
    <style>
        {self.COVERLETTER_CSS_STYLES}
    </style>
</head>
<body>
"""

        self.HTML_FOOTER_COVERLETTR = """\
</body>
</html>
"""


    def _humanize_text(self, text:str) -> str:
        TRANSLATION_TABLE = str.maketrans({
            "—": ",",
            "–": "-",
            "‑": "-",
            "…": "...",
            "“": '"',
            "”": '"',
            "‘": "'",
            "’": "'",
            "\u00A0": " ",
        })
        return text.translate(TRANSLATION_TABLE)

    def make_coverletter(self, personal_info: Dict[str, Any], candidate_profile: str, job_desc: str, company_info: Optional[CompanyInfo] = None) -> str:
        """Generate a tailored cover letter using the LLM.

        Args:
            personal_info: Dictionary containing personal details (e.g., name, contact).
                This information is **not** sent to the LLM to avoid privacy leakage.
            candidate_profile: full text about candidate.
            job_desc: The job description data as a dictionary.
            company_info: Optional dictionary containing company information.

        Returns:
            A string representing the tailored cover letter in markdown format.

        The method loads the ``make_coverletter`` prompt template from ``llm_prompts.yaml``,
        constructs a user message that includes the ``profile``, ``job_desc``, and ``company_info`` JSON payloads,
        and calls the LLM client to obtain a markdown cover letter. The response is expected to
        be plain text (markdown).
        """

        logger.info("Generating cover letter using LLM")
        make_coverletter_prompt = self.prompts.get('make_coverletter')
        system_prompt = make_coverletter_prompt.get('system')
        user_template = make_coverletter_prompt.get(f'user_{self.language}')

        # Prepare user content: include candidate profile, job description, and company info
        user_content = (
            f"\n<candidate_profile>\n{candidate_profile}\n</candidate_profile>\n\n"
            f"\n<job_description>\n{job_desc}\n</job_description>\n\n"
            f"\n<company_information>\n{company_info.model_dump()}\n</company_information>\n\n" if company_info else ""
            f"{user_template}"
        )
        logger.debug(f"Cover letter generation user content prepared. Language: {self.language}")


        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        # Call the LLM to generate the tailored cover letter
        logger.info("Calling LLM to generate cover letter")
        response = self.llm.create_completion(
            messages=messages,
            use_case="cover_letter_generation"
        )
        logger.debug("LLM response received for cover letter generation")

        # Extract generated markdown text
        if "choices" in response and response["choices"]:
            generated = response["choices"][0].get("message", {}).get("content", "")
            coverletter_markdown = self._humanize_text(generated.strip())
            logger.debug("Cover letter markdown generated successfully")
        else:
            error_msg = "LLM did not return a valid response for cover letter generation"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Add personal info to the generated cover letter
        logger.info("Adding personal information to cover letter")
        coverletter_markdown = self._add_personal_info_to_coverletter(coverletter_markdown, personal_info)
        logger.info("Cover letter generation completed successfully")

        return coverletter_markdown

    def _add_personal_info_to_coverletter(self, coverletter_markdown: str, personal_info: Dict[str, Any]) -> str:
        """Add personal information to the generated cover letter markdown.

        Args:
            coverletter_markdown: The generated cover letter markdown string.
            personal_info: Dictionary containing personal details (e.g., name, contact).
        

        Returns:
            The cover letter markdown with personal information added at the end.
        """
        logger.debug("Adding personal information to cover letter markdown")

        if not personal_info:
            return coverletter_markdown

        # Extract personal info
        name = personal_info.get("basics", {}).get("name", "")

        # Add personal info at the end
        coverletter_markdown = f"{coverletter_markdown}\n\nBest regards,\n\n{name}"
        return coverletter_markdown

    def make_cv(self, personal_info: Dict[str, Any], cv: Dict[str, Any], job: JobInfo) -> str:
        """Generate a tailored CV using the LLM.

        Args:
            personal_info: Dictionary containing personal details (e.g., name, contact).
                This information is **not** sent to the LLM to avoid privacy leakage.
            cv: The base CV data (e.g., work experience, education) as a dictionary.
            job: The job description data as a JobInfo instance.

        Returns:
            A markdown string representing the tailored CV.

        The method loads the ``make_cv`` prompt template from ``llm_prompts.yaml``,
        constructs a user message that includes the ``cv`` and ``job`` JSON payloads,
        and calls the LLM client to obtain a markdown CV. The response is expected to
        be plain text (markdown).
        """
        logger.info("Generating tailored CV using LLM")

        make_cv_prompt = self.prompts.get('make_CV')
        system_prompt = make_cv_prompt.get('system')
        user_template = make_cv_prompt.get(f'user_{self.language}')

        logger.info("Preparing CV generation prompt")
        # Prepare the user content: include CV and job JSON data
        user_content = (f"{user_template}\n\nCV Data:\n{cv}\n\nJob Data:\n{job}")
        user_content = (
            f"\n<candidate_cv>\n{cv}\n</candidate_cv>\n\n"
            f"\n<target_job_info>\n{job}\n</target_job_info>\n\n"
            f"{user_template}\n\n"
        )
        logger.debug(f"CV generation user content prepared. Language: {self.language}")
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        # Call the LLM to generate the tailored CV
        logger.info("Calling LLM to generate CV")
        response = self.llm.create_completion(
            messages=messages,
            use_case="cv_generation"
        )
        logger.debug("LLM response received for CV generation")

        # Extract generated markdown text
        if "choices" in response and response["choices"]:
            generated = response["choices"][0].get("message", {}).get("content", "")
            cv_markdown = self._humanize_text(generated.strip())
            logger.debug("CV markdown generated successfully")
        else:
            error_msg = "LLM did not return a valid response for CV generation"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Add personal info to the generated CV
        logger.info("Adding personal information to CV")
        cv_markdown = self._add_personal_info_CV(cv_markdown, personal_info, job)
        logger.info("CV generation completed successfully")

        return cv_markdown

    def _add_personal_info_CV(self, cv_markdown: str, personal_info: Dict[str, Any], job: Optional[JobInfo] = None) -> str:
        """Add personal information to the generated CV markdown.

        Args:
            cv_markdown: The generated CV markdown string.
            personal_info: Dictionary containing personal details (e.g., name, contact).
            job: Optional JobInfo instance to extract location from.

        Returns:
            The CV markdown with personal information added at the top.
        """
        logger.debug("Adding personal information to CV markdown")
        if not personal_info:
            return cv_markdown

        # Extract personal info
        name = personal_info.get("basics", {}).get("name", "")
        email = personal_info.get("basics", {}).get("email", "")
        phone = personal_info.get("basics", {}).get("phone", "")
        location = personal_info.get("basics", {}).get("location", {})
        
        # Determine location based on USER_CONFIG setting
        user_config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'USER_CONFIG.json')
        
        try:
            with open(user_config_path, 'r', encoding='utf-8') as f:
                user_config = json.load(f)
                location_source = user_config.get('location', 'user')
        except Exception:
            location_source = 'user'
        
        # Get location from job if configured and available
        if location_source == "job" and job:
            if job.country and job.location:
                # Both country and location are available
                country = job.country
                city = job.location.split(',')[0].strip()
            elif job.country:
                # Only country is available
                country = job.country
                city = ""
                print(f"\nCountry found in job: {country}")
                city = input("Please enter the city for the job location: ").strip()
            elif job.location:
                # Only location is available
                city = job.location.split(',')[0].strip()
                location_parts = job.location.split(',')
                country = location_parts[-1].strip() if len(location_parts) > 1 else ""
                if not country:
                    print(f"\nLocation found in job: {city}")
                    country = input("Please enter the country for the job location: ").strip()
            else:
                # Neither country nor location found in job
                print("\nNo location information found in the job description.")
                city = input("Please enter the city for the job location: ").strip()
                country = input("Please enter the country for the job location: ").strip()
        else:
            # Use user's location from personal_info
            city = location.get("city", "") 
            country = location.get("country", "")

        # Build clickable contact links so the CV
        contact = " | ".join([
            f"{self.CONTACT_MARKER}",
            f"{city}, {country}",
            f"[{phone}](tel:{phone})",
            f"[{email}](mailto:{email})"
        ])
        
        # Create personal info section
        personal_info_section = f'# {name}\n{contact}'

        # add personal info 
        cv_markdown = cv_markdown.replace(r"# {personal_info}", personal_info_section)
        return cv_markdown
    
    def find_wanted_skills(self):
        """find what skills are needed but is missing in the cv"""
        pass

    def _format_inline(self, text: str) -> str:
        """
        Fast inline markdown formatter.
        Avoids unnecessary regex work.
        """

        if "`" in text:
            text = self.CODE_RE.sub(r"<code>\1</code>", text)

        if "**" in text:
            text = self.BOLD_RE.sub(r'<span class="bold">\1</span>', text)

        if "_" in text:
            text = self.ITALIC_RE.sub(r'<span class="italic">\1</span>', text)

        return text

    def make_html_cv(self, markdown: str) -> str:
        """Convert markdown CV to HTML.

        Args:
            markdown: The markdown CV content to convert.

        Returns:
            str: HTML representation of the CV.
        """
        logger.info("Converting CV markdown to HTML")

        if not markdown or not markdown.strip():
            error_msg = "Markdown content is empty or invalid"
            logger.error(error_msg)
            raise ValueError(error_msg)

        logger.debug("Processing CV markdown content")
        html_parts = [self.HTML_HEADER_CV]
        append = html_parts.append

        lines = markdown.splitlines()
        n = len(lines)
        i = 0

        while i < n:
            line = lines[i].strip()

            if not line:
                i += 1
                continue
                
            logger.debug(f"Processed {n} lines of CV markdown")
            # --------------------
            # Headings
            # --------------------
            if line.startswith("### "):
                append(
                    f'<h3 class="section-heading">{line[4:].strip()}</h3>'
                )
                i += 1
                continue

            if line.startswith("## "):
                append(
                    f'<h2 class="subtitle">{line[3:].strip()}</h2>'
                )
                i += 1
                continue

            if line.startswith("# "):
                append(
                    f'<h1 class="title">{line[2:].strip()}</h1>'
                )
                i += 1
                continue

            # --------------------
            # contact info
            # --------------------
            if line.startswith(f"{self.CONTACT_MARKER} "):
                append(self._render_contact_info_html_cv(line))
                i += 1
                continue

            # --------------------
            # CV Entry
            #
            # #### Senior Data Scientist | Nimbus Commerce GmbH | Berlin, Germany
            # _02/2022 – Present_
            #
            # OR
            #
            # #### Senior Data Scientist | Nimbus Commerce GmbH
            # _02/2022 – Present_
            # --------------------

            if line.startswith("#### "):

                header = line[5:].strip()

                parts = [p.strip() for p in header.split("|")]

                title = parts[0] if len(parts) > 0 else ""
                company = parts[1] if len(parts) > 1 else ""
                location = parts[2] if len(parts) > 2 else ""

                j = i + 1

                while j < n and not lines[j].strip():
                    j += 1

                date_line = ""

                if j < n:

                    date_line = lines[j].strip()

                    if (
                        date_line.startswith("_")
                        and date_line.endswith("_")
                    ):
                        date_line = date_line[1:-1].strip()

                append(
                    f"""
                    <table class="cv-entry">
                        <tr>
                            <td class="cv-left">
                                <div class="cv-title">{self._format_inline(title)}</div>
                                {f'<div class="cv-company">{self._format_inline(company)}</div>' if company else ''}
                            </td>

                            <td class="cv-right">
                                {f'<div class="cv-location">{self._format_inline(location)}</div>' if location else ''}
                                {f'<div class="cv-date">{self._format_inline(date_line)}</div>' if date_line else ''}
                            </td>
                        </tr>
                    </table>
                    """
                )

                i = j + 1
                continue

            # --------------------
            # Group consecutive bullets (support "- " and "* " markers)
            # --------------------
            if line.startswith("- ") or line.startswith("* "):
                append("<ul>")

                while i < n:
                    bullet = lines[i].strip()

                    # Break when the line is not a bullet marker of either type
                    if not (bullet.startswith("- ") or bullet.startswith("* ")):
                        break

                    # Remove the first two characters (marker and space)
                    content = self._format_inline(
                        bullet[2:].strip()
                    )

                    append(f"<li>{content}</li>")

                    i += 1

                append("</ul>")
                continue

            # --------------------
            # Regular paragraph
            # --------------------
            append(
                f'<p class="normal">{self._format_inline(line)}</p>'
            )

            i += 1

        append(self.HTML_FOOTER_CV)
        
        result = "".join(html_parts)
        logger.info("CV HTML conversion completed successfully")
        
        return result

    def make_html_coverletter(self, markdown: str) -> str:
        """Convert markdown cover letter to HTML.

        The implementation mirrors :py:meth:`make_html_CV` but without the CV‑specific
        entry handling. It supports headings (``#``, ``##``, ``###``), unordered
        bullet lists (lines starting with ``- ``) and regular paragraphs. Inline
        markdown for **bold**, *italic* and ``code`` is transformed via
        :py:meth:`_format_inline`.
        """
        logger.info("Converting cover letter markdown to HTML")

        if not markdown or not markdown.strip():
            raise ValueError("Markdown content is empty or invalid")

        html_parts = [self.HTML_HEADER_COVERLETTR]
        append = html_parts.append

        lines = markdown.splitlines()
        n = len(lines)
        i = 0

        while i < n:
            line = lines[i].strip()

            if not line:
                i += 1
                continue

            # Headings -------------------------------------------------------
            if line.startswith("### "):
                append(f'<h3 class="section-heading">{line[4:].strip()}</h3>')
                i += 1
                continue

            if line.startswith("## "):
                append(f'<h2 class="subtitle">{line[3:].strip()}</h2>')
                i += 1
                continue

            if line.startswith("# "):
                append(f'<h1 class="title">{line[2:].strip()}</h1>')
                i += 1
                continue

            # Unordered list -------------------------------------------------
            if line.startswith("- ") or line.startswith("* "):
                append("<ul>")
                while i < n:
                    bullet = lines[i].strip()
                    if not (bullet.startswith("- ") or bullet.startswith("* ")):
                        break
                    content = self._format_inline(bullet[2:].strip())
                    append(f"<li>{content}</li>")
                    i += 1
                append("</ul>")
                continue

            # Paragraph ------------------------------------------------------
            append(f'<p class="normal">{self._format_inline(line)}</p>')
            i += 1

        # Footer
        append(self.HTML_FOOTER_COVERLETTR)
        return "".join(html_parts)

    def _render_contact_info_html_cv(self, line: str) -> str:
        """
        Convert:
        <<contact info>> City, Country | [phone](tel:+1-555-0100) | [email](mailto:a@b.com) | [LinkedIn](https://...)
        to:
        <div class="contact-info">City, Country <span class="sep">|</span> <a href="tel:...">phone</a> ...</div>
        """
        import re

        # Marker at start of the line
        contact_marker_re = re.compile(
            r'^\s*' + re.escape(self.CONTACT_MARKER) + r'\s*',
            re.IGNORECASE
        )

        # Correct markdown link regex: [label](url)
        md_link_re = re.compile(r'\[([^\]]+)\]\(([^)\s]+)\)')

        # 1) Remove marker
        raw = contact_marker_re.sub("", line).strip()

        # 2) Split by pipes into segments
        parts = [p.strip() for p in raw.split("|") if p.strip()]

        rendered_parts: list[str] = []

        for part in parts:
            # Replace markdown links with <a href="...">label</a>
            def repl(m: re.Match) -> str:
                label = m.group(1).strip()
                href = m.group(2).strip()

                # normalize tel:
                if href.lower().startswith("tel:"):
                    tel_value = href[4:].strip()
                    tel_norm = re.sub(r"[^\d+]", "", tel_value)
                    href = f"tel:{tel_norm}"

                return f'<a href="{href}">{label}</a>'

            part_html = md_link_re.sub(repl, part)

            # Apply your inline formatting to remaining text (bold/italic/code)
            part_html = self._format_inline(part_html)

            rendered_parts.append(part_html)

        sep = ' <span class="sep">|</span> '
        return f'<div class="contact-info">{sep.join(rendered_parts)}</div>'

    def make_pdf(self, html: str, path: str, base_url: Optional[str] = None) -> None:
        """Generate a PDF file from HTML content using *WeasyPrint*.

        Architecture: HTML → PDF

        WeasyPrint uses Pango/HarfBuzz for text shaping (the same engine used
        by GTK and LibreOffice), which renders the Garamond variable font and
        abbreviation-heavy text correctly, supports flexbox properly, and is
        actively maintained.

        Args:
            html: The HTML content to convert to PDF.
            path: The file path where the PDF will be saved.
            base_url: Base path used to resolve relative asset URLs referenced
                inside the HTML/CSS (e.g. the ``../fonts/...`` path in the
                ``@font-face`` rule). Defaults to the directory containing this
                source file, which matches the relative path used in
                ``HTML_HEADER_CV``. Pass an explicit value if the HTML is
                rendered from a different location.

        Raises:
            ValueError: If the HTML content is empty or invalid.
            RuntimeError: If PDF generation fails.
        """
        from weasyprint import HTML
        import os

        if not html or not html.strip():
            raise ValueError("HTML content is empty or invalid")

        # Ensure the output directory exists
        output_dir = os.path.dirname(path)
        if output_dir and not os.path.isdir(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        if base_url is None:
            base_url = os.path.dirname(os.path.abspath(__file__))

        # Convert HTML string directly to PDF
        try:
            HTML(string=html, base_url=base_url).write_pdf(path)
        except Exception as e:
            raise RuntimeError(f"Failed to generate PDF with WeasyPrint: {e}")

    def test_cv(self, cv: str, job: JobInfo, show_missing_skills: bool = True) -> CVScoreResponse:
        """Score CV compatibility with job description and identify missing skills using LLM.

        Args:
            cv: The generated CV markdown string
            job: JobInfo instance containing job requirements
            show_missing_skills: Whether to identify and save missing skills to a file

        Returns:
            CVScoreResponse object containing compatibility score, missing skills, explanation, and strengths

        The response is expected to be JSON format.
        """
        logger.info("Testing CV compatibility with job requirements")


        # Prepare user content: include CV and job data
        content = (
            f"\n<candidate_cv>\n{cv}\n</candidate_cv>\n\n"
            f"\n<job_description>\n{job.description}\n</job_description>\n\n"
        )

        # Call the LLM to get compatibility score and missing skills
        logger.info("Calling LLM to score CV compatibility")
        response = self.llm.model_parser(content, CVScoreResponse(), "score_cv_missing_skills", 'cv_scoring')
        logger.debug("LLM response received for CV scoring")

        return response


