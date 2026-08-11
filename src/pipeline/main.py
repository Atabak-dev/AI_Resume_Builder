import os
import sys
from typing import Optional, Dict
from dotenv import load_dotenv
import json
import yaml
import re
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

from src.utils.scraper import WikipediaScraper, CompanyWebsiteScraper
from src.pipeline.generator import Generator_Handler
from src.pipeline.models import CompanyInfo, JobInfo

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

    The function scans the ``personal_info`` dictionary (which may contain nested
    structures) for leaf string values. Each value is removed from ``text`` in a
    case‑insensitive manner. To catch occurrences where the value is embedded in
    a longer word (e.g. ``"Alpha Beta"`` appearing as ``"text_alpha_beta_text"``),
    the function also creates a pattern that allows any combination of spaces,
    underscores or hyphens between the words.

    Args:
        text: The original text that may contain personal data.
        personal_info: The dictionary loaded from ``personal_info.json``.

    Returns:
        The text with all detected personal information stripped out.
    """
    logger.debug("Removing personal information from text")

    # Helper to flatten the dict and collect leaf string values
    def _collect_strings(obj):
        strings = []
        if isinstance(obj, dict):
            for v in obj.values():
                strings.extend(_collect_strings(v))
        elif isinstance(obj, list):
            for item in obj:
                strings.extend(_collect_strings(item))
        elif isinstance(obj, (str, int, float)):
            # Convert non‑string scalars to string for replacement
            strings.append(str(obj))
        return strings
    
    # Gather all personal data strings
    personal_strings = set(_collect_strings(personal_info))
    logger.debug(f"Collected {len(personal_strings)} personal information strings for removal")

    cleaned_text = text
    for value in personal_strings:
        if not value:
            continue
        # Escape regex special characters
        escaped = re.escape(value)
        # Direct replacement (case‑insensitive)
        cleaned_text = re.sub(escaped, "", cleaned_text, flags=re.IGNORECASE)

        # If the value contains spaces, also replace variants with underscores or hyphens
        if " " in value:
            # Build a pattern that matches the words separated by any of [_\s-]
            parts = [re.escape(part) for part in value.split()]
            pattern = r"[_\s-]+".join(parts)
            cleaned_text = re.sub(pattern, "", cleaned_text, flags=re.IGNORECASE)

    return cleaned_text

def _official_site_scraping(llm, company_name):
    website_timeout = user_config.get('scraping', {}).get('company_website_timeout', 15)
    website_scraper = CompanyWebsiteScraper(timeout=website_timeout)
    homepage_url = website_scraper.find_official_website(company_name)
    website_text = ""
    if homepage_url:
        print(f"Discovered possible official website: {homepage_url}")
        # Fetch homepage and internal links
        page_data = website_scraper.extract_text_and_links(homepage_url)
        homepage_text = page_data.get("text", "")
        links = page_data.get("links", [])

        # Prepare a compact description of links for the LLM
        import yaml as _yaml
        with open('llm_prompts.yaml', 'r', encoding='utf-8') as f:
            prompts = _yaml.safe_load(f)
        nav_system = prompts.get('website_navigation', {}).get('system', '')

        # Build a simple JSON-like description of links
        links_preview = [
            {"url": l["url"], "anchor": l.get("anchor", "")}
            for l in links[:50]
        ]

        messages = [
            {"role": "system", "content": nav_system},
            {
                "role": "user",
                "content": f"HOMEPAGE_SNIPPET:\n{homepage_text[:4000]}\n\nLINKS:\n{links_preview}",
            },
        ]

        nav_response = llm.create_completion(messages=messages, use_case="data_extraction")
        try:
            nav_content = nav_response["choices"][0]["message"]["content"]
            import json as _json
            candidates = _json.loads(nav_content)
        except Exception:
            candidates = []

        # Always include homepage text
        website_text_parts = [f"=== PAGE: {homepage_url} ===\n{homepage_text}"]

        # For each candidate URL, ask user approval before fetching
        for item in candidates:
            url = item.get("url")
            reason = item.get("reason", "")
            if not url:
                continue
            print("\nProposed page:")
            print(f"URL   : {url}")
            print(f"Reason: {reason}")
            approve = input("Press Enter to fetch this page, or type anything to skip: ")
            if approve.strip() != "":
                print("Skipped.")
                continue
            page = website_scraper.extract_text_and_links(url)
            text = page.get("text", "")
            if not text:
                continue
            website_text_parts.append(f"\n\n=== PAGE: {url} ===\n{text}")

        website_text = "".join(website_text_parts)
        return website_text

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

    # Get company info from Wikipedia 
    company_name = input("Enter company name to search on Wikipedia: ")
    if company_name:
        logger.info(f"Searching for company information on Wikipedia: {company_name}")
        # Wikipedia
        wiki = WikipediaScraper(language=language)
        print("Loading Wikipedia page ...")
        company_info_text = wiki.extract_page_text(company_name)
        logger.info(f"Wikipedia page loaded successfully for: {company_name}")
        print("Wikipedia page loaded successfully")

        # Official website scraping with user-approved page access
        website_text = "" #_official_site_scraping(llm, company_name)

        combined_context = company_info_text
        if website_text:
            combined_context = f"{company_info_text}\n\n{website_text}"

        print("Extracting company data from Wikipedia and website ...")
        logger.info("Extracting company data using LLM")
        company_info = llm.model_parser(combined_context, CompanyInfo(),'company_info', 'company_extraction')
        logger.info(f"Company data extraction successful. Company: {company_info.name}")
        print("Company data extraction successful")
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
    
    logger.info(f"Creating output folder for company: {company_info.name}")
    fileHandel.make_output_folder(company_info.name)
    logger.info(f"Output folder created: {fileHandel.output_path}")

    # Reuse the already extracted structured company_info instead of duplicating work
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
        cv_score_response = generator.test_cv(
            cv=cv_markdown,
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