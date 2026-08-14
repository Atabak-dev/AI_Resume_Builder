import os
import sys
from typing import Optional, Dict
from dotenv import load_dotenv
import json
import yaml
from llm_client import LLM_Handeler
import logging

# Load environment variables
load_dotenv()

# Add src directory to Python path
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, src_path)

# Configure logging with rotation to prevent disk space issues
from logging.handlers import RotatingFileHandler

# Create logs directory if it doesn't exist
logs_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'logs')
os.makedirs(logs_dir, exist_ok=True)

# Configure log rotation: max 5MB per file, keep 5 backup files
log_file_path = os.path.join(logs_dir, 'application.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(
            log_file_path,
            maxBytes=5*1024*1024,  # 5MB
            backupCount=5,
            encoding='utf-8'
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.info("=== Application started ===")
logger.info(f"Logging configured with rotation. Log file: {log_file_path}")

# Import utils after adding src to path
from src.utils.file_handler import FileHandler
from src.utils.privacy import PersonalInfoScrubber
from src.utils.search import get_search_provider
from src.utils.scraper import WikipediaScraper, CompanyWebsiteScraper
from src.pipeline.generator import Generator_Handler
from src.pipeline.models import CompanyInfo, JobInfo
from src.pipeline.llm_client import ToolsUnsupportedError
from src.pipeline.tools import HostApprovalGate, ResearchToolbox

# Load USER_CONFIG.json for language and paths
user_config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'USER_CONFIG.json')
user_config = {}
with open(user_config_path, 'r', encoding='utf-8') as f:
    user_config = json.load(f)


def _api_key_loader() -> str:
    """Load LLM API key from environment variables.
    
    Returns:
        str: The API key
        
    Raises:
        ValueError: If API key is not set in environment
    """
    logger.info("Loading LLM API key from environment variables...")
    llm_api_key = os.getenv('LLM_API_KEY')
    
    if not llm_api_key:
        error_msg = "LLM_API_KEY must be set in .env file. Please ensure the .env file exists and contains the LLM_API_KEY variable."
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    logger.info("LLM API key loaded successfully")
    return llm_api_key

def _llm_config_loader():
    """Load LLM configuration from YAML file.
    
    Returns:
        dict: The loaded configuration
    """
    logger.info("Loading LLM configuration from llm_prompts.yaml...")
    try:
        with open('llm_prompts.yaml', 'r', encoding='utf-8') as f:
            llm_config = yaml.safe_load(f)
        logger.info("LLM configuration loaded successfully")
        return llm_config
    except FileNotFoundError as e:
        logger.error(f"Configuration file not found: {e}")
        raise
    except Exception as e:
        logger.error(f"Error loading LLM configuration: {e}")
        raise

def _select_language(llm_config) -> str:
    """Determine language for CV and cover letter generation.
    
    Args:
        llm_config: Configuration dictionary
        
    Returns:
        str: Selected language code ('en' or 'de')
    """
    logger.info("Selecting language for CV and cover letter generation")
    print("\nSelect language for CV and cover letter generation:")
    llm_config.get("languages")
    language_choice = input(f"Enter your choice (e,d, Enter Last used:{user_config.get('language', 'en')}): ").strip().lower()
    if language_choice == 'e':
        language = 'en'
    elif language_choice == 'd':
        language = 'de'
    else:
        language = user_config.get('language', 'en')

    logger.info(f"Selected language: {language}")
    print(f"selected language: {language}")
    return language

def _CV_loader(data_dir):
    """Load CV and personal information data from JSON files.
    
    Args:
        data_dir: Directory containing the data files
        
    Returns:
        tuple: (cv_data, personal_info_data) dictionaries
    """
    logger.info(f"Loading CV and personal information data from {data_dir}")
    
    cv_file_path = os.path.join(data_dir, 'cv.json')
    personal_info_file_path = os.path.join(data_dir, 'personal_info.json')
    cv_data = {}
    personal_info_data = {}
    
    # Load CV data
    if os.path.exists(cv_file_path):
        try:
            with open(cv_file_path, 'r', encoding='utf-8') as f:
                cv_data = json.load(f)
            logger.info(f"Loaded CV data from: {cv_file_path}")
            print(f"Loaded CV data from: {cv_file_path}")
        except Exception as e:
            logger.error(f"Error loading CV file: {e}")
            print(f"Error loading CV file: {e}")
    else:
        logger.warning(f"CV file not found at: {cv_file_path}")
        print(f"CV file not found at: {cv_file_path}")
    
    # Load personal info data
    if os.path.exists(personal_info_file_path):
        try:
            with open(personal_info_file_path, 'r', encoding='utf-8') as f:
                personal_info_data = json.load(f)
            logger.info(f"Loaded personal info from: {personal_info_file_path}")
            print(f"Loaded personal info from: {personal_info_file_path}")
        except Exception as e:
            logger.error(f"Error loading personal info file: {e}")
            print(f"Error loading personal info file: {e}")
    else:
        logger.warning(f"Personal info file not found at: {personal_info_file_path}")
        print(f"Personal info file not found at: {personal_info_file_path}")

    if not cv_data or not personal_info_data:
        logger.warning("CV data or personal info data is missing. Continuing with limited functionality...")
        print("\nWarning: CV data or personal info data is missing. Continuing with limited functionality...")

    return cv_data, personal_info_data

def _remove_personal_info(text: str, personal_info: dict) -> str:
    """Remove any personal information found in *text*.

    Thin wrapper around :class:`~src.utils.privacy.PersonalInfoScrubber`, kept
    for call-site compatibility. See that class for the matching rules.
    """
    logger.debug("Removing personal information from text")
    return PersonalInfoScrubber(personal_info).scrub(text)

def _official_site_scraping(llm, toolbox: ResearchToolbox, homepage_url: str, prompts: dict) -> str:
    """Fallback research path used when the endpoint has no tool-calling support.

    Single-shot `website_navigation` prompt picks a handful of candidate links
    off the homepage, then each is fetched through *toolbox*, which enforces
    the same host-approval gate, privacy scrub, and URL trace as the
    tool-calling path.
    """
    homepage_result = toolbox.fetch_page(homepage_url, reason="Official company homepage")
    if homepage_result.get("status") != "ok":
        return ""

    links_preview = [
        {"url": l["url"], "anchor": l.get("anchor", "")}
        for l in homepage_result.get("links", [])[:50]
    ]
    nav_system = prompts.get('website_navigation', {}).get('system', '')
    messages = [
        {"role": "system", "content": nav_system},
        {"role": "user", "content": f"HOMEPAGE_SNIPPET:\n{homepage_result['text'][:4000]}\n\nLINKS:\n{links_preview}"},
    ]

    try:
        nav_response = llm.create_completion(messages=messages, use_case="company_research")
        nav_content = nav_response["choices"][0]["message"]["content"]
        candidates = json.loads(nav_content)
    except Exception as e:
        logger.warning(f"Website navigation call failed: {e}")
        candidates = []

    for item in candidates:
        url = item.get("url")
        if not url:
            continue
        toolbox.fetch_page(url, reason=item.get("reason", ""))

    return toolbox.dossier


def _research_company(llm, company_name: str, scrubber: PersonalInfoScrubber, language: str, prompts: dict):
    """Gather company background via web search + Wikipedia.

    Runs the LLM tool-calling loop (web_search / fetch_page / wikipedia_page)
    when the endpoint supports it; falls back to a Wikipedia lookup plus the
    website-navigation flow above otherwise. Set `research.enabled: false` in
    USER_CONFIG.json to skip straight to a plain Wikipedia lookup (e.g. for
    offline use).

    Returns:
        (context_text, source_urls)
    """
    research_cfg = user_config.get('research', {})

    if not research_cfg.get('enabled', True):
        wiki = WikipediaScraper(language=language)
        text = wiki.extract_page_text(company_name)
        return scrubber.scrub(text, min_length=3), []

    scraping_cfg = user_config.get('scraping', {})
    gate = HostApprovalGate()
    provider = get_search_provider()
    wiki = WikipediaScraper(language=language)
    site = CompanyWebsiteScraper(
        timeout=scraping_cfg.get('company_website_timeout', 30),
        request_delay=scraping_cfg.get('request_delay', 1.0),
        max_retries=scraping_cfg.get('max_retries', 3),
    )
    toolbox = ResearchToolbox(
        scrubber=scrubber, gate=gate, provider=provider, wiki=wiki, site=site,
        max_page_chars=research_cfg.get('max_page_chars', 12000),
        max_fetches=research_cfg.get('max_fetches', 8),
    )

    system_prompt = prompts.get('company_research', {}).get('system', '')
    user_prompt = f"Company: {company_name}\nLanguage preference: {language}"

    summary = ""
    try:
        result = llm.run_tool_loop(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            toolbox, use_case="company_research",
            max_iterations=research_cfg.get('max_iterations', 6),
        )
        summary = result.get("content", "")
    except ToolsUnsupportedError:
        logger.warning("Endpoint does not support tool calling; using the fallback research path.")
        print("Endpoint has no tool-calling support - falling back to manual website discovery.")
        title = wiki.resolve_title(company_name)
        if title:
            toolbox.wikipedia_page(title)
        homepage = site.find_official_website(company_name, provider)
        if homepage:
            _official_site_scraping(llm, toolbox, homepage, prompts)

    sources = toolbox.sources
    if sources:
        print(f"\nAccessed {len(sources)} page(s) for {company_name}:")
        for s in sources:
            print(f"  {s}")

    parts = [p for p in (toolbox.dossier, f"=== RESEARCH SUMMARY ===\n{summary}" if summary.strip() else "") if p.strip()]
    return "\n\n".join(parts), sources

def main():
    """Main function to run the job application pipeline with LLM integration."""
    logger.info("=== Job Application Pipeline with LLM Integration ===")
    print("=== Job Application Pipeline with LLM Integration ===\n")
    
    try:
        # Load LLM credentials from environment ==================================
        logger.info("Starting application initialization")
        
        llm_api_key = _api_key_loader()
        logger.info("LLM API key loaded successfully")
        
        llm = LLM_Handeler(llm_api_key)
        logger.info("LLM handler initialized")
        
        llm_config = _llm_config_loader()
        logger.info("LLM configuration loaded")
    except Exception as e:
        logger.error(f"Error loading llm information: {e}")
        raise

    language = _select_language(llm_config)
    logger.info(f"Language selected: {language}")
    
    # Determine paths for data and outputs
    data_dir = user_config.get('paths', {}).get('data', 'data')
    outputs_dir = user_config.get('paths', {}).get('outputs', 'outputs')
    logger.info(f"Data directory: {data_dir}, Outputs directory: {outputs_dir}")
    
    fileHandel = FileHandler(outputs_dir)
    logger.info("FileHandler initialized")
    
    # Initialize Generator_Handler with language support
    generator = Generator_Handler(llm, language, llm_config)
    logger.info("Generator_Handler initialized")
    
    # Load CV and personal info data =======================================================
    logger.info("Loading CV and personal information data")
    cv_data, personal_info_data = _CV_loader(data_dir)

    # Get job and company description ==============================================================
    logger.info("Collecting job description from user input")
    print("Please paste the job description text (press Enter twice to finish):")
    job_description_lines = []
    while True:
        line = input()
        if line == "":
            if len(job_description_lines) > 0 and job_description_lines[-1] == "":
                break
        job_description_lines.append(line)
    
    job_description = "\n".join(job_description_lines[:-1])
    logger.info(f"Job description collected. Length: {len(job_description)} characters")
    
    if not job_description or len(job_description.strip()) < 50:
        logger.warning("Job description is too short. Continuing with limited matching...")
        print("\nWarning: Job description is too short. Continuing with limited matching...")

    # Remove personal info from job description
    logger.info("Removing personal information from job description")
    job_description = _remove_personal_info(job_description, personal_info_data)
    logger.info("Personal information removed from job description")

    # populate job objects
    logger.info("Extracting job information using LLM")
    print("Extracting work position ...")
    try:
        job: JobInfo = llm.model_parser(job_description, JobInfo(),'job_info', 'job_extraction')
        logger.info(f"Job information extracted successfully. Title: {job.title}")
        print("Work position extraction successful")
    except Exception as e:
        logger.error(f"Error extracting job information: {e}")
        raise

    # Get company name from the extracted job info, falling back to a manual prompt
    company_name = (job.company_common_name or job.company_name or "").strip()
    if company_name:
        logger.info(f"Company name extracted from job description: {company_name}")
        print(f"Detected company: {company_name}")
    else:
        logger.warning("No company name found in job description; asking user")
        company_name = input("Company name could not be detected. Enter it manually (or press Enter to skip): ").strip()

    if company_name:
        confirmed_name = input(
            f"Press Enter to research '{company_name}', or type the correct company name: "
        ).strip()
        if confirmed_name:
            company_name = confirmed_name
        logger.info(f"Researching company information for: {company_name}")

        scrubber = PersonalInfoScrubber(personal_info_data)
        print("Researching company (web search + Wikipedia) ...")
        combined_context, company_sources = _research_company(llm, company_name, scrubber, language, llm_config)

        if combined_context.strip():
            print("Extracting company data from research ...")
            logger.info("Extracting company data using LLM")
            company_info = llm.model_parser(combined_context, CompanyInfo(), 'company_info', 'company_extraction')
            logger.info(f"Company data extraction successful. Company: {company_info.name}")
            print("Company data extraction successful")
        else:
            logger.warning("No research context gathered; using the name only.")
            company_info = CompanyInfo(name=company_name)

        company_info.sources = company_sources
    else:
        logger.warning("No company name provided. Using empty company info")
        company_info = CompanyInfo()
    
    # Save language preference to USER_CONFIG.json
    if user_config.get('language') != language:
        try:
            logger.info(f"Updating language preference to USER_CONFIG.json: {language}")
            with open(user_config_path, 'w', encoding='utf-8') as f:
                json.dump(user_config, f, indent=2, ensure_ascii=False)
            print(f"Saved language preference to USER_CONFIG.json: {language}")
        except Exception as e:
            logger.error(f"Error saving USER_CONFIG: {e}")
            print(f"Error saving USER_CONFIG: {e}")
    
    logger.info(f"Creating output folder for company: {company_name}")
    fileHandel.make_output_folder(company_name)
    logger.info(f"Output folder created: {fileHandel.output_path}")

    # Reuse the already extracted structured company_info instead of duplicating work,
    # but prefer the commonly known name extracted from the job description over the
    # Wikipedia-derived name (which is often the parent group, not the hiring entity)
    if company_name:
        company_info.name = company_name
    job.company = company_info
    logger.info(f"Job company set to: {company_info.name}")

    logger.info("Saving job data to files")
    fileHandel.save_yaml(job.model_dump(), "job.yaml")
    logger.info("Job data saved to job.yaml")
    
    fileHandel.save_raw_text(job_description, "job_description.txt")
    logger.info("Job description saved to job_description.txt")

    # Generate tailored documents
    logger.info("Generating tailored CV using LLM")
    print('Using LLM to tailor the CV ...')
    try:
        cv_markdown = generator.make_cv(
            personal_info=personal_info_data,
            cv=cv_data,
            job=job
        )
        logger.info("Markdown CV created successfully")
        print('Markdown CV created successful')
        
        fileHandel.save_markdown(cv_markdown, 'cv.md')
        logger.info("CV markdown saved to cv.md")
        
        print('making CV html to structure markdown ...')
        cv_html = generator.make_html_cv(cv_markdown)
        logger.info("CV HTML created successfully")
        
        fileHandel.save_raw_text(cv_html, "cv_raw.html")
        logger.info("CV HTML saved to cv_raw.html")
        
        print('making CV PDF ...')
        pdf_file_path = fileHandel.make_file_caption(
            personal_info_data.get("basics", "").get("name", ""), 
            job.title, 
            'CV'
        )
        logger.info(f"Generating PDF for CV: {pdf_file_path}")
        
        generator.make_pdf(cv_html, pdf_file_path)
        logger.info(f'PDF of CV saved to {pdf_file_path}')
        print(f'PDF of CV is saved to {pdf_file_path}')
    except Exception as e:
        logger.error(f"Error generating CV: {e}")
        raise
    
    # Test CV compatibility and save missing skills analysis
    logger.info("Testing CV compatibility with job requirements")
    print('\n=== Testing CV Compatibility ===')
    try:
        # Score the LLM's own output, not `cv_markdown` - the latter already has
        # name, e-mail, phone and location merged in, and test_cv() sends the CV
        # back to the LLM.
        cv_score_response = generator.test_cv(
            cv=generator.cv_markdown_raw,
            job=job,
            show_missing_skills=True
        )
        logger.info("CV compatibility test completed successfully")
        
        # Save the score and missing skills to file
        fileHandel.save_missing_skills(
            cv_score_response=cv_score_response
        )
        logger.info("Missing skills analysis saved to file")
    except Exception as e:
        logger.error(f"Error testing CV compatibility: {e}")
        raise
    

    # Cover letter
    logger.info("Generating cover letter")
    profile_file_path = os.path.join(data_dir, 'profile.txt')
    profile = ""
    if os.path.exists(profile_file_path):
        try:
            with open(profile_file_path, 'r', encoding='utf-8') as f:
                profile = f.read()
            logger.info(f"Loaded candidate profile from: {profile_file_path}")
            print(f"Loaded candidate profile from: {profile_file_path}")
        except Exception as e:
            logger.error(f"Error loading profile file: {e}")
            print(f"Error loading profile file: {e}")
    else:
        logger.warning(f"Profile file not found at: {profile_file_path}")
        print(f"Profile file not found at: {profile_file_path}")
    
    print("Using LLM to tailor the cover letter ...")
    try:
        coverletter_markdown = generator.make_coverletter(
            personal_info=personal_info_data,
            candidate_profile=profile,
            job_desc=job_description,
            company_info=company_info
        )
        logger.info("Cover letter markdown created successfully")
        print('Markdown cover letter created successful')
        
        fileHandel.save_markdown(coverletter_markdown, 'coverletter.md')
        logger.info("Cover letter saved to coverletter.md")
        
        print('making cover letter html to structure markdown ...')
        coverletter_html = generator.make_html_coverletter(coverletter_markdown)
        logger.info("Cover letter HTML created successfully")
        print('cover letter html created successful')
        
        print('making cover letter PDF ...')
        pdf_file_path = fileHandel.make_file_caption(
            personal_info_data.get("basics", "").get("name", ""), 
            job.title, 
            'CoverLetter'
        )
        logger.info(f"Generating PDF for cover letter: {pdf_file_path}")
        
        generator.make_pdf(coverletter_html, pdf_file_path)
        logger.info(f'PDF of cover letter saved to {pdf_file_path}')
        print(f'PDF of cover letter is saved to {pdf_file_path}')
    except Exception as e:
        logger.error(f"Error generating cover letter: {e}")
        raise



if __name__ == "__main__":
    try:
        main()
        logger.info("=== Application completed successfully ===")
        
    except Exception as e:
        logger.error(f"=== Application failed with error: {e} ===", exc_info=True)
        print(f"\nApplication failed: {e}")
        raise