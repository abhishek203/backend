import asyncio
from typing import Optional
from contextlib import AsyncExitStack
import logging
import os
from datetime import datetime

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()  # load environment variables from .env

# Configure logging
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

class MCPClient:
    def __init__(self, user_id, ticket_id):
        # Initialize session and client objects
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()
        self.anthropic = Anthropic()
        self.user_id = user_id
        self.ticket_id = ticket_id
        
        # Configure logger for this user
        self.logger = logging.getLogger(f"mcp_client_{user_id}")
        self.logger.setLevel(logging.INFO)
        
        # Create user-specific log directory
        user_log_dir = os.path.join(log_dir, f"user_{user_id}")
        if not os.path.exists(user_log_dir):
            os.makedirs(user_log_dir)
        
        # Create file handler with user-specific log file
        log_file = os.path.join(user_log_dir, f"mcp_client_{datetime.now().strftime('%Y%m%d')}.log")
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.INFO)
        
        # Create formatter
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        
        # Add handler to logger
        self.logger.addHandler(file_handler)
        
    async def connect_to_server(self, server_script_path: str = 'analysisagent/server.py'):
        """Connect to an MCP server

        Args:
            server_script_path: Path to the server script (.py or .js)
        """
        is_python = server_script_path.endswith('.py')
        is_js = server_script_path.endswith('.js')
        if not (is_python or is_js):
            raise ValueError("Server script must be a .py or .js file")

        command = "python3" if is_python else "node"
        server_params = StdioServerParameters(
            command=command,
            args=[server_script_path],
            env={"USER_ID" : self.user_id}
        )

        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(ClientSession(self.stdio, self.write))

        await self.session.initialize()

        # List available tools
        response = await self.session.list_tools()
        tools = response.tools
    
    async def process_query(self, query: str) -> str:
        """Process a query using Claude and available tools"""
        system_prompt = await self.session.get_prompt(
                "analysis_system_prompt"
            )
        
        # Extract prompt text from the message content
        prompt_text = None
        if hasattr(system_prompt, 'messages') and isinstance(system_prompt.messages, list) and len(system_prompt.messages) > 0:
            message = system_prompt.messages[0]
            if hasattr(message, 'content'):
                content = message.content
                if hasattr(content, 'text'):
                    prompt_text = content.text
        
        messages = [
            {
                "role": "user",
                "content": query
            }
        ]

        response = await self.session.list_tools()
        available_tools = [{
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.inputSchema
        } for tool in response.tools]

        # Initial Claude API call
        response = self.anthropic.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1000,
            messages=messages,
            tools=available_tools
        )
        
        # Process response and handle tool calls
        final_text = []
        
        while True:
            assistant_message_content = []
            for content in response.content:
                if content.type == 'text':
                    self.logger.debug(f"Received text content: {content.text[:100]}...")
                    final_text.append(content.text)
                    assistant_message_content.append(content)
                elif content.type == 'tool_use':
                    tool_name = content.name
                    tool_args = content.input
                    self.logger.info(f"Tool use detected: {tool_name}")

                    # Execute tool call
                    result = await self.session.call_tool(tool_name, tool_args)
                    final_text.append(f"[Calling tool {tool_name} with args {tool_args}]")

                    assistant_message_content.append(content)
                    messages.append({
                        "role": "assistant",
                        "content": assistant_message_content
                    })
                    messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": content.id,
                                "content": result.content
                            }
                        ]
                    })
                    
                    # Get next response from Claude
                    response = self.anthropic.messages.create(
                        model="claude-3-5-sonnet-20241022",
                        max_tokens=1000,
                        messages=messages,
                        tools=available_tools
                    )
                    # Break out of the current loop to process the new response
                    break
            else:
                # If no tool_use was found in the response, we're done
                break
        
        response_summary = self.anthropic.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1000,
            system=f"Provide a summary of the analysis conducted for the query: {query} based on the below messages",
            messages=messages
        )
        response_text = response_summary.content[0].text
        print(response_text)
        return response_text
        
    async def cleanup(self):
        """Clean up resources"""
        await self.exit_stack.aclose()