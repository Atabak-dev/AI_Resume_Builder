import http.client
import os
import yaml
import json
import logging
from typing import Dict, List, Optional, Any

# Configure logger
logger = logging.getLogger(__name__)


class LLM_Handeler:
    """
    A client for interacting with the LLM API endpoint.
    
    This class provides low-level access to the LLM API without any
    prompt templates or business logic. All prompt construction should
    be handled by the calling code.
    """

    def __init__(self, api_key: str):
        """
        Initialize the LLM client.

        Args:
            api_key: API key for authentication
            endpoint: Base URL for the LLM API
        """
        logger.info("Initializing LLM client")
        self.api_key = api_key
        
        # Load model from llm_config.json
        logger.info("Loading LLM configuration from llm_config.json")
        try:
            with open('llm_config.json', 'r') as f:
                config = json.load(f)
            # Connection details are private: environment variables win over the
            # (committed) config file so no personal endpoint ends up in git.
            self.model = os.getenv('LLM_MODEL') or config.get('model')
            self.endpoint = os.getenv('LLM_ENDPOINT') or config.get('endpoint')
            self.host = os.getenv('LLM_HOST') or config.get('host')
            self.base_path = os.getenv('LLM_BASE_PATH') or config.get('base_path')
            self.general_settings = config.get('general_settings', {})
            self.use_cases = config.get('use_cases', {})

            if not self.host or not self.base_path:
                error_msg = (
                    "LLM host/base_path not configured. Set LLM_HOST and LLM_BASE_PATH "
                    "in your .env file (see .env.example)."
                )
                logger.error(error_msg)
                raise ValueError(error_msg)

            logger.info(f"LLM client initialized successfully. Model: {self.model}")
        except FileNotFoundError as e:
            error_msg = f"LLM configuration file not found: {e}"
            logger.error(error_msg)
            raise
        except Exception as e:
            error_msg = f"Error loading LLM configuration: {e}"
            logger.error(error_msg)
            raise


    def create_completion(
        self,
        messages: List[Dict[str, Any]],
        response_format: Optional[Dict] = None,
        use_case: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a completion using the LLM API.

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            response_format: Optional response format specification
            use_case: Identifier for the specific use case being invoked

        Returns:
            Dictionary containing the response from the LLM
        """
        logger.info(f"Creating LLM completion for use case: {use_case}")
        
        if not messages:
            error_msg = "Messages cannot be empty"
            logger.error(error_msg)
            raise ValueError(error_msg)

        logger.debug(f"Preparing LLM request with {len(messages)} messages")
        
        
        logger.info(f"Connecting to LLM API at {self.host}")
        conn = http.client.HTTPSConnection(self.host, timeout=60)

        # Prepare payload
        payload = {
            "messages": messages,
            "model": self.model,
        }
            
        # Determine settings based on use_case or provided parameters
        if use_case and use_case in self.use_cases:
            use_case_settings = self.use_cases[use_case]
            temperature = use_case_settings.get("temperature", self.general_settings.get("temperature"))
            max_completion_tokens = use_case_settings.get("max_tokens", self.general_settings.get("max_tokens"))
            stream = use_case_settings.get("stream", self.general_settings.get("stream"))
            reasoning_effort = use_case_settings.get("reasoning_effort", self.general_settings.get("reasoning_effort"))
        else:
            temperature = self.general_settings.get("temperature")
            max_completion_tokens = self.general_settings.get("max_tokens")
            stream = self.general_settings.get("stream")
            reasoning_effort = self.general_settings.get("reasoning_effort")
            
        # Include response format if supplied (e.g., JSON schema)
        if response_format is not None:
            payload["response_format"] = response_format

        if temperature is not None:
            payload["temperature"] = temperature

        if reasoning_effort is not None:
            payload["reasoning_effort"] = reasoning_effort

        if stream is not None:
            payload["stream"] = stream

        if max_completion_tokens is not None:
            payload["max_completion_tokens"] = max_completion_tokens

        logger.debug("Preparing LLM request headers")
        headers = {
            'Content-Type': "application/json",
            'Authorization': f"Bearer {self.api_key}"
        }
        # with open("Output.txt", "w") as text_file:
        #     text_file.write(json.dumps(payload))
        logger.info("Sending request to LLM API")
        conn.request("POST", self.base_path, json.dumps(payload), headers)

        res = conn.getresponse()
        data = res.read()
        logger.debug(f"Received response from LLM API with status {res.status}")

        if res.status != 200:
            error_msg = f"LLM API request failed with status {res.status}: {data.decode('utf-8')}"
            logger.error(error_msg)
            raise ConnectionError(error_msg)
            
        try:
            response_data = json.loads(data.decode("utf-8"))
            logger.debug("LLM API response parsed successfully")
            return response_data
        except json.JSONDecodeError as e:
            error_msg = f"Failed to parse LLM API response: {e}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        except Exception as e:
            error_msg = f"Error processing LLM API response: {e}"
            logger.error(error_msg)
            raise

    def model_parser(self, context: str, model_instance, prompt: str, use_case:str) -> Any:
        """
        Get a model class as schema and ask LLM to fill that schema from prompt.
        If a value cannot be found in the prompt, set it to None.

        Args:
            context: The context text to extract information from
            model_instance: The dataclass model to fill with extracted data

        Returns:
            An instance of the model_schema filled with extracted data
        """
        logger.info("Parsing model data using LLM")
        # Load the model_parse prompt template
        try:
            with open('llm_prompts.yaml', 'r') as f:
                pre_prompts = yaml.safe_load(f)
            logger.debug("Model parsing prompt template loaded successfully")
        except FileNotFoundError as e:
            error_msg = f"Prompt template file not found: {e}"
            logger.error(error_msg)
            raise
        except Exception as e:
            error_msg = f"Error loading prompt template: {e}"
            logger.error(error_msg)
            raise
        
        try:
            system_prompt = pre_prompts.get('model_parse', {}).get(prompt, {}).get('system', None)
            if system_prompt is None:
                error_msg = "System prompt not found in prompt template"
                logger.error(error_msg)
                raise ValueError(error_msg)
        except Exception as e:
            error_msg = f"Error retrieving system prompt: {e}"
            logger.error(error_msg)
            raise

        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "schema": model_instance.get_schema(),
                "strict": True
            }
        }

        user_prompt = pre_prompts.get('model_parse', {}).get(prompt, {}).get('user', '')

        # Create an instance of the model schema
        model_instance_local = model_instance
        prompt = (f"schema:{response_format}\n"
                  f"context:{context}\n\n"
                  f"{user_prompt}"
                  )

        # Prepare the full prompt with the template and user prompt
        messages = [
            {
                'role': 'system',
                'content': system_prompt
            },
            {
                'role': 'user',
                'content': prompt
            }
        ]
        
        # Create completion using the LLM
        response = self.create_completion(
            messages=messages,
            use_case="data_extraction",
            response_format=response_format
        )
         
        
        try:
            if "choices" not in response or not response["choices"]:
                raise ValueError("No choices found in LLM response")

            message = response["choices"][0].get("message", {})
            content = message.get("content", "")

            if not content or not content.strip():
                raise ValueError("LLM returned empty content for JSON schema")

            # content is expected to be a JSON string; parse into the model
            # with open('test.json', 'w',encoding='utf-8') as f:
            #     f.write(str(content))
            model_instance_local = model_instance_local.set_from_json(content)
            return model_instance_local

        except (json.JSONDecodeError, TypeError, ValueError) as e:
            raise ValueError(f"Failed to parse LLM response: {str(e)}")
    