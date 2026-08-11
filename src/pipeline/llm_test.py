import os
import sys
from dotenv import load_dotenv

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.pipeline.llm_client import LLM_Handeler

# Load environment variables
load_dotenv()

def test_llm_client():
    """Test the LLM client with a simple greeting."""
    
    # Initialize LLM client
    llm_client = LLM_Handeler(
        api_key=os.getenv("LLM_API_KEY"), # type: ignore
    )
    
    
    # Create a simple greeting message
    messages = [
        {
            "role": "user",
            "content": "Return the word hello."
        }
    ]
    
    
    # Create completion
    response = llm_client.create_completion(
        messages=messages,
        use_case="quick_test"
    )
    
    # Print response
    print("\nLLM Response:")
    print(response)
    
    # Extract and print the greeting
    if "choices" in response and len(response["choices"]) > 0:
        greeting = response["choices"][0]["message"]["content"]
        print("\nGreeting from LLM:")
        print(greeting)
    else:
        print("\nCould not extract greeting from response")

if __name__ == "__main__":
    test_llm_client()