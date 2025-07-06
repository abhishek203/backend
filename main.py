from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from analysisagent.client import MCPClient as AnalysisMCPClient
from executionagent.client import MCPClient as ExecutionMCPClient
from pydantic import BaseModel
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import StreamingResponse, RedirectResponse
import httpx
import base64
import json
import os
import asyncio

from config.settings import (
    CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, FRONTEND_URL,
    CORS_ORIGINS, CORS_METHODS, CORS_HEADERS, CORS_MAX_AGE
)

from database.mongodb import db

API_BASE = "http://3.22.181.115"

app = FastAPI(title="MCP Agent API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*", "ngrok-skip-browser-warning"],  # Allows all headers plus ngrok header
)


class AnalysisRequest(BaseModel):
    user_id: str
    description: str
    ticket_id: str

def get_salesforce_urls(environment: str = "production"):
    if environment == "sandbox":
        return {
            "auth_url": "https://test.salesforce.com/services/oauth2/authorize",
            "token_url": "https://test.salesforce.com/services/oauth2/token"
        }
    else:
        return {
            "auth_url": "https://login.salesforce.com/services/oauth2/authorize",
            "token_url": "https://login.salesforce.com/services/oauth2/token"
        }

@app.get("/connect")
async def connect_to_salesforce(environment: str = Query(..., description="Environment for Salesforce connection"), 
                              userId: str = Query(..., description='user id')):
    state_data = {
        "userId": userId,
        "environment": environment
    }
    state = base64.b64encode(json.dumps(state_data).encode()).decode()
    
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "full refresh_token",
        "state": state
    }
    urls = get_salesforce_urls(environment)
    auth_url = f"{urls['auth_url']}?{httpx.QueryParams(params)}"
    return RedirectResponse(auth_url)

@app.get("/oauth/callback")
async def oauth_callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing required parameters")
    
    try:
        state_data = json.loads(base64.b64decode(state.encode()).decode())
        user_id = state_data.get("userId")
        environment = state_data.get("environment")
        
        if not user_id or not environment:
            raise HTTPException(status_code=400, detail="Invalid state parameter")
            
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid state parameter: {str(e)}")

    token_data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "code": code,
    }

    urls = get_salesforce_urls(environment)
    async with httpx.AsyncClient() as client:
        response = await client.post(urls['token_url'], data=token_data)
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to obtain access token")
        token_response = response.json()
        
        await db.update_user_salesforce_tokens(
            user_id,
            token_response.get("access_token"),
            token_response.get("refresh_token"),
            token_response.get("instance_url")
        )

        # Call the new_user endpoint
        try:
            headers = {
                'accept': 'application/json',
                'Content-Type': 'application/json'
            }
            print(f"Making request to: {API_BASE}/new_user/{user_id}")
            print(f"With headers: {headers}")
            
            # Configure client with longer timeout
            async with httpx.AsyncClient(timeout=30.0) as client:
                for attempt in range(3):  # Try up to 3 times
                    try:
                        new_user_response = await client.post(
                            f"{API_BASE}/new_user/{user_id}",
                            headers=headers,
                            json={}
                        )
                        print(f"Response status: {new_user_response.status_code}")
                        print(f"Response headers: {new_user_response.headers}")
                        print(f"Response content: {new_user_response.text}")
                        
                        if new_user_response.status_code == 200:
                            break  # Success, exit retry loop
                        elif new_user_response.status_code >= 500:
                            print(f"Server error, attempt {attempt + 1} of 3")
                            if attempt < 2:  # Don't sleep on last attempt
                                await asyncio.sleep(1)  # Wait before retry
                            continue
                        else:
                            print(f"Warning: Failed to call new_user endpoint: {new_user_response.status_code}")
                            print(f"Response content: {new_user_response.text}")
                            break  # Don't retry on client errors
                    except httpx.ReadTimeout:
                        print(f"Timeout on attempt {attempt + 1} of 3")
                        if attempt < 2:  # Don't sleep on last attempt
                            await asyncio.sleep(1)  # Wait before retry
                        continue
                    except Exception as e:
                        print(f"Unexpected error on attempt {attempt + 1}: {str(e)}")
                        break  # Don't retry on unexpected errors
                        
        except Exception as e:
            print(f"Warning: Error calling new_user endpoint: {str(e)}")
            print(f"Error type: {type(e)}")
            import traceback
            print(f"Traceback: {traceback.format_exc()}")
        
        return RedirectResponse(f'{FRONTEND_URL}/excel-tasks')

@app.post("/analysis-agent")
async def analysis_agent(request: AnalysisRequest):
    client = AnalysisMCPClient(request.user_id, request.ticket_id)
    try:
        await client.connect_to_server()
        return await client.process_query(request.description)
    finally:
        await client.cleanup()
        
@app.post("/chat-agent")
async def chat_agent(request: AnalysisRequest):
    client = AnalysisMCPClient(request.user_id, request.ticket_id)
    try:
        await client.connect_to_server()
        return await client.process_query(request.description)
    finally:
        await client.cleanup()
        
@app.post("/execution-agent")
async def analysis_agent(request: AnalysisRequest):
    client = ExecutionMCPClient(request.user_id, request.ticket_id)
    try:
        await client.connect_to_server()
        return await client.process_query(request.description)
    finally:
        await client.cleanup()

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
# structure the result for analysis