"""
Models for personal information and CV data structures.
"""

from typing import Annotated, Dict, List, Any
from pydantic import BaseModel, Field
import json

from pydantic.json_schema import SkipJsonSchema


class CVScoreResponse(BaseModel):
    """
    Class to store CV compatibility score and missing skills analysis.
    """
    compatibility_score: int = Field(
        default=0,
        description="Compatibility score between 0 and 100",
        ge=0,
        le=100
    )
    missing_skills: List[str] = Field(
        default_factory=list,
        description="List of missing skills/requirements from the job description"
    )
    strengths: List[str] = Field(
        default_factory=list,
        description="List of CV strengths that match the job requirements"
    )

    @classmethod
    def get_schema(cls) -> dict:
        """
        Returns the JSON schema for this model.
        
        Returns:
            A dictionary containing the JSON schema
        """
        return cls.model_json_schema()

    @classmethod
    def set_from_json(cls, json_text) -> 'CVScoreResponse':
        """
        Parse a JSON string and create a :class:`CVScoreResponse` instance.

        The JSON is expected to contain the keys ``compatibility_score``,
        ``missing_skills``, ``explanation`` and ``strengths``. Missing keys are
        replaced with the model's default values.

        Args:
            json_text: A JSON formatted string representing a CVScoreResponse.

        Returns:
            An instantiated ``CVScoreResponse`` populated with the parsed data.
        """
        try:
            data = json.loads(json_text)
            return cls(
                compatibility_score=data.get('compatibility_score', 0),
                missing_skills=data.get('missing_skills', []),
                strengths=data.get('strengths', [])
            )
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(f"Failed to parse CVScoreResponse JSON: {str(e)}")
    


class CompanyInfo(BaseModel):
    """
    Class to store and manage company information.
    """
    name: str = Field(
        default="",
        description="The official name of the company",
        examples=["Microsoft Corporation", "Google LLC", "Apple Inc."]
    )
    major_product_or_service: str = Field(
        default="",
        description="The primary product or service offered by the company",
        examples=["Windows operating system, Azure cloud services", "Search engine, Online advertising, Cloud computing"]
    )
    industry: str = Field(
        default="",
        description="The industry/sector the company operates in",
        examples=["Technology, Software", "Information Technology, Internet Services"]
    )
    description: str = Field(
        default="",
        description="A brief description of the company's business and operations",
        examples=["A multinational technology company specializing in Internet-related services and products"]
    )
    size: str = Field(
        default="",
        description="Company size in terms of employees or revenue",
        examples=["10,000+ employees", "Fortune 500", "150,000+ employees worldwide"]
    )
    location: str = Field(
        default="",
        description="Headquarters location",
        examples=["Redmond, Washington, USA", "Mountain View, California, USA"]
    )
    website: str = Field(
        default="",
        description="Official company website URL",
        examples=["https://www.microsoft.com", "https://www.google.com"]
    )
    founded: str = Field(
        default="",
        description="Year company was founded",
        examples=["1975", "1998"]
    )
    products: List[str] = Field(
        default_factory=list,
        description="List of major products/services offered",
        examples=[["Windows", "Office", "Azure"], ["Search", "Chrome", "Android"]]
    )
    future: str = Field(
        default="",
        description="Future outlook or predictions about the company",
        examples=["Expanding into AI and cloud computing", "Focusing on artificial intelligence and quantum computing research"]
    )
    sources: SkipJsonSchema[List[str]] = Field(
        default_factory=list,
        description="URLs actually fetched during research. Populated locally, never by the LLM."
    )

    @classmethod
    def get_schema(cls) -> dict:
        """
        Returns a detailed explanation of what each field should contain for LLM guidance.

        Returns:
            A formatted string explaining each field's purpose and expected content
        """
        schema_explanation = CompanyInfo.model_json_schema()
        return schema_explanation

    @classmethod
    def set_from_json(cls, json_text) -> 'CompanyInfo':
        """
        Parse data from JSON text to fill the class fields.
        
        Args:
            json_text: JSON string containing company data
            
        Returns:
            A CompanyInfo instance populated with data from the JSON text
        """
        
        try:
            data = json.loads(json_text)
            
            # Create a new CompanyInfo instance with data from JSON
            return cls(
                name=data.get('name', ''),
                major_product_or_service=data.get('major_product_or_service', ''),
                industry=data.get('industry', ''),
                description=data.get('description', ''),
                size=data.get('size', ''),
                location=data.get('location', ''),
                website=data.get('website', ''),
                founded=data.get('founded', ''),
                products=data.get('products', []),
                future=data.get('future', ''),
                sources=data.get('sources', []),
            )
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(f"Failed to parse JSON: {str(e)}")
    

class JobInfo(BaseModel):
    """
    Class to store and manage job information.
    """
    title: str = Field(
        default="",
        description="The job position title", 
        examples=["Software Engineer", "Data Scientist", "Product Manager"]
        )
    description: str = Field(
        default="",
        description="Detailed description of the job responsibilities and requirements",
        examples=["Develop and maintain software applications using Python and JavaScript. Requires 3+ years of experience in full-stack development."]
    )
    location: str = Field(
        default="",
        description="Geographic location of the job",
        examples=["New York, NY", "San Francisco, CA", "Remote", "Hybrid"]
    )
    country: str = Field(
        default="",
        description="Country of the job location",
        examples=["USA", "Germany", "France"]
    )
    type: str = Field(
        default="",
        description="Employment type",
        examples=["Full-time", "Part-time", "Contract", "Internship", "Freelance"]
    )  # Full-time, Part-time, Contract, Internship, etc.
    salary_range: str = Field(
        default="",
        description="Compensation range or salary information",
        examples=["$80,000 - $120,000 per year", "$40 - $60 per hour", "Competitive salary"]
    )
    company_name: str = Field(
        default="",
        description=(
            "Official legal name of the hiring company exactly as stated in the job "
            "description, including any legal form suffix. If the hiring company is "
            "anonymized or the posting is from a recruiting agency, leave this empty."
        ),
        examples=["Carl Zeiss AG", "Sopra Steria SE", ""]
    )
    company_common_name: str = Field(
        default="",
        description=(
            "The short, commonly known name of the same company as used in everyday "
            "speech and press - the legal form suffix and any parent-group qualifiers "
            "removed. If the company is only ever known by its full name, repeat it here. "
            "Leave empty only if no company is named."
        ),
        examples=["Zeiss", "Sopra Steria", ""]
    )
    company: CompanyInfo = Field(
        default_factory=CompanyInfo,
        description="Company information refer to CompanyInfo"
    )
    
    @classmethod
    def get_schema(cls) -> dict:
        """
        Returns a detailed explanation of what each field should contain for LLM guidance.
        
        Returns:
            A formatted string explaining each field's purpose and expected content
        """
        schema_explanation = JobInfo.model_json_schema()
        # remove companyInfo
        schema_explanation.pop('$defs')
        schema_explanation['properties'].pop('company')
        return schema_explanation

    @classmethod
    def set_from_json(cls, json_text, companyInfo: CompanyInfo = CompanyInfo()) -> 'JobInfo':
        """
        Parse data from JSON text to fill the class fields.
        
        Args:
            json_text: JSON string containing job data
            companyInfo: Optional CompanyInfo instance to use for the company field
            
        Returns:
            A JobInfo instance populated with data from the JSON text
        """
        
        try:
            data = json.loads(json_text)
            
            # Use provided companyInfo or create a new one
            company = companyInfo if companyInfo else CompanyInfo()
            
            # Create a new JobInfo instance with data from JSON
            return cls(
                title=data.get('title', ''),
                description=data.get('description', ''),
                location=data.get('location', ''),
                country=data.get('country', ''),
                type=data.get('type', ''),
                salary_range=data.get('salary_range', ''),
                company_name=data.get('company_name', ''),
                company_common_name=data.get('company_common_name', ''),
                company=company
            )
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(f"Failed to parse JSON: {str(e)}")
