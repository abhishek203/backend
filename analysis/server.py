from typing import Any
import httpx
from mcp.server.fastmcp import FastMCP
import os

# Initialize FastMCP server
mcp = FastMCP("analysis")

# Constants
API_BASE = "http://3.22.181.115"  

user_id = os.environ.get("USER_ID")

async def make_request(url: str, method: str = "GET", data: dict = None) -> dict[str, Any] | None:
    """Make a request to the API with proper error handling."""
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        try:
            if method == "GET":
                response = await client.get(url, headers=headers, timeout=30.0)
            else:
                response = await client.post(url, headers=headers, json=data, timeout=30.0)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error making request: {e}")
            return None

@mcp.tool()
async def read_file(file_path: str) -> str:
    """Read a file from the specified path.

    Args:
        user_id: The user ID to read the file for
        file_path: The path of the file to read
    """
    url = f"{API_BASE}/read_file/{user_id}?file_path={file_path}"
    data = await make_request(url)
    
    if not data:
        return "Unable to read file. Please check the file path and try again."
    
    return str(data)

@mcp.tool()
async def retrieve_metadata(metadata_type: str, file_name: str = "*") -> str:
    """Retrieve metadata of a specific type.

    Args:
        user_id: The user ID to retrieve metadata for
        metadata_type: The type of metadata to retrieve (e.g. Flow, CustomObject, etc.)
        file_name: Optional file name pattern to filter results - ex: "Mobile__c"
    """
    url = f"{API_BASE}/retrieve_metadata/{user_id}"
    data = {
        "metadata_type": metadata_type,
        "file_name": file_name
    }
    
    response = await make_request(url, method="POST", data=data)
    
    if not response:
        return f"Unable to retrieve {metadata_type} metadata. Please check the parameters and try again."
    
    return str(response)


@mcp.prompt()
def analysis_system_prompt() -> str:
    """ Analysis System Prompt"""
    return """You are a seasoned Salesforce expert, given a problem/query come up with plan to solve the problem or answer the query using the tools available to you.
    Your output should be in markdown.
 """

if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport='stdio')
