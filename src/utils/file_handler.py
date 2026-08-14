import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Literal
import yaml
import re
import logging

# Configure logger
logger = logging.getLogger(__name__)

class FileHandler:
    """
    A class to handle file operations.
    Initializes a folder with datetime and company name to track CVs.
    """

    def __init__(self, base_path: str = "outputs"):
        """
        Initialize the FileHandler with a base path.

        Args:
            base_path (str): The base directory where company folders will be created. Defaults to "outputs".
        """
        logger.info(f"Initializing FileHandler with base path: {base_path}")
        self.output_base_path = os.path.join(os.path.dirname(__file__), '..', '..', base_path)
        logger.debug(f"FileHandler output base path set to: {self.output_base_path}")
    
    def make_output_folder(self, company_name: str) -> str:
        """
        Create a folder named with the current datetime and company name.

        Args:
            company_name (str): The name of the company for the folder

        Returns:
            str: The path to the created folder
        """
        logger.info(f"Creating output folder for company: {company_name}")
        # Get current datetime in yy-mm-dd-HH-MM format
        datetime_str = datetime.now().strftime("%y%m%d-%H%M")
        safe_company_name = re.sub(r'[\\/:*?"<>|]+', '_', (company_name or "").strip())[:60].strip(" ._")
        folder_name = f"{datetime_str}_{safe_company_name}" if safe_company_name else datetime_str
        
        # Create the full path
        folder_path = os.path.join(self.output_base_path, folder_name)
        
        # Create the directory if it doesn't exist
        logger.debug(f"Creating directory: {folder_path}")
        os.makedirs(folder_path, exist_ok=True)
        
        self.output_path = folder_path
        logger.info(f"Output folder created: {folder_path}")
        return folder_path

    def make_file_caption(self, name: str, job_title: str, doc: Literal['CV', 'CoverLetter']):
        """Generate a file caption for PDF output.
        
        Args:
            name: Candidate's name
            job_title: Job title
            doc: Document type ('CV' or 'CoverLetter')
            
        Returns:
            str: Formatted file caption
        """
        logger.info(f"Generating file caption for {doc}: {name} - {job_title}")
        name_part = "_".join([part.capitalize() for part in name.split()])
        
        # Remove (f/m/d), dashes, underscores, and other special characters from job_title
        job_title = re.sub(r'[\-\s_]+', ' ', job_title)
        job_title = re.sub(r'\([^)]*\)', '', job_title)
        job_title = "_".join(job_title.split())
        
        file_path = os.path.join(self.output_path,
                                  f"{doc}_{name_part}_{job_title}.pdf"
                                  )
        return file_path

    def save_markdown(self, markdown: str, file_name: str = "CV.md") -> str:
        """
        Save markdown content to a file in the output directory.

        Args:
            markdown (str): The markdown content to save
            file_name (str): The name of the file to save (default: "CV.md")

        Returns:
            str: The full path to the saved file
        """
        logger.info(f"Saving markdown file: {file_name}")
        # Create the full file path
        file_path = os.path.join(self.output_path, file_name)
        logger.debug(f"Markdown file path: {file_path}")
        
        # Write the markdown content to the file
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(markdown)
            logger.info(f"Markdown file saved successfully: {file_path}")
            print(f"Markdown file saved successfully at: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Error saving markdown file {file_name}: {e}")
            raise

    def save_raw_text(self, content: str, file_name: str = "raw.txt") -> str:
        """
        Save raw content to a text file inside a 'raw' subdirectory.

        Args:
            content (str): The raw content to save
            file_name (str): The name of the file to save (default: "raw.txt")

        Returns:
            str: The full path to the saved file
        """
        logger.info(f"Saving raw text file: {file_name}")
        # Create raw subdirectory path
        raw_dir = os.path.join(self.output_path, "raw")
        logger.debug(f"Creating raw directory: {raw_dir}")
        os.makedirs(raw_dir, exist_ok=True)
        
        # Create the full file path
        file_path = os.path.join(raw_dir, file_name)
        logger.debug(f"Raw text file path: {file_path}")
        
        # Write the raw content to the file
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"Raw text file saved successfully: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Error saving raw text file {file_name}: {e}")
            raise


    def save_yaml(self, data, file_name: str = "data.yaml") -> str:
        """
        Save data as YAML to a file in the main output directory.

        Args:
            data (dict): The data to save as YAML
            file_name (str): The name of the file to save (default: "data.yaml")

        Returns:
            str: The full path to the saved file
        """
        logger.info(f"Saving YAML file: {file_name}")
        
        # Create the full file path
        file_path = os.path.join(self.output_path, file_name)
        logger.debug(f"YAML file path: {file_path}")
        
        # Write the YAML content to the file
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            logger.info(f"YAML file saved successfully: {file_path}")
            print(f"YAML file saved successfully at: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Error saving YAML file {file_name}: {e}")
            raise
        
    def save_missing_skills(self, cv_score_response) -> str:
        """
        Save missing skills analysis to a text file in the output directory.

        Args:
            cv_score_response: CVScoreResponse object containing score and missing skills

        Returns:
            str: The full path to the saved missing_skills.txt file
        """
        logger.info("Saving missing skills analysis")
        
        # Create the full file path
        file_path = os.path.join(self.output_path, "missing_skills.txt")
        logger.debug(f"Missing skills file path: {file_path}")
        
        # Write missing skills analysis to file
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write("=== CV Compatibility Analysis ===\n\n")
                f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
                
                # Save compatibility score
                f.write(f"Compatibility Score: {cv_score_response.compatibility_score}/100\n\n")
                logger.debug(f"Saved compatibility score: {cv_score_response.compatibility_score}/100")

                # Save keyword match (locally computed; absent on older CVScoreResponse instances)
                matched_keywords = getattr(cv_score_response, 'matched_keywords', [])
                missing_keywords = getattr(cv_score_response, 'missing_keywords', [])
                total_keywords = len(matched_keywords) + len(missing_keywords)
                if total_keywords:
                    percentage = getattr(cv_score_response, 'keyword_match_percentage', 0)
                    f.write(f"Keyword Match: {percentage}% ({len(matched_keywords)} of {total_keywords} job keywords)\n\n")
                    if matched_keywords:
                        f.write("Matched Keywords:\n")
                        f.write(f"  {', '.join(matched_keywords)}\n\n")
                    if missing_keywords:
                        f.write("Missing Keywords:\n")
                        f.write(f"  {', '.join(missing_keywords)}\n\n")
                    logger.debug(f"Saved keyword match: {percentage}% ({len(matched_keywords)}/{total_keywords})")

                # Save strengths
                if cv_score_response.strengths:
                    f.write("Strengths:\n")
                    for strength in cv_score_response.strengths:
                        f.write(f"  • {strength}\n")
                    f.write("\n")
                    logger.debug(f"Saved {len(cv_score_response.strengths)} strengths")
                
                # Save missing skills
                if cv_score_response.missing_skills:
                    f.write("Missing Skills:\n")
                    for i, skill in enumerate(cv_score_response.missing_skills, 1):
                        f.write(f"  {i}. {skill}\n")
                    logger.debug(f"Saved {len(cv_score_response.missing_skills)} missing skills")
                else:
                    f.write("No missing skills identified. Your CV matches the job requirements well!\n")
                    logger.debug("No missing skills identified")
            
            logger.info(f"Missing skills analysis saved successfully: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Error saving missing skills analysis: {e}")
            raise


    