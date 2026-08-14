import os
import sys
from dotenv import load_dotenv

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.pipeline.llm_client import LLM_Handeler, ToolsUnsupportedError

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


def test_tool_calling():
    """Check whether the configured endpoint supports OpenAI-style tool calling.

    Sends one message with a trivial `get_time` tool and reports whether the
    endpoint calls it, ignores it, or rejects the request outright. This is
    the fastest way to know if the company-research tool loop (main.py) will
    work against a given endpoint, before spending real tokens on it.
    """
    llm_client = LLM_Handeler(api_key=os.getenv("LLM_API_KEY"))  # type: ignore

    tools = [{
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Return the current time.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    }]
    messages = [{"role": "user", "content": "What time is it? Use the get_time tool."}]

    print("\nProbing endpoint for tool-calling support ...")
    try:
        response = llm_client.create_completion(messages=messages, use_case="quick_test", tools=tools)
    except ToolsUnsupportedError as e:
        print(f"Endpoint REJECTED the request when `tools` was included: {e}")
        print("-> The company-research feature will use its fallback (no tool loop) with this endpoint.")
        return

    message = response.get("choices", [{}])[0].get("message", {})
    if message.get("tool_calls"):
        print(f"Endpoint SUPPORTS tool calling. tool_calls: {message['tool_calls']}")
    else:
        print("Endpoint accepted `tools` but did not call one (silent-ignore).")
        print(f"content: {message.get('content', '')!r}")
        print("-> The company-research tool loop will exit after one turn with thin results.")


if __name__ == "__main__":
    if "--tools" in sys.argv:
        test_tool_calling()
    else:
        test_llm_client()