from dotenv import load_dotenv
from anthropic import Anthropic
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from typing import List, Dict, TypedDict
from contextlib import AsyncExitStack
import json
import asyncio
from openai import OpenAI
import json
import os

load_dotenv()

class ToolDefinition(TypedDict):
    name: str
    description: str
    input_schema: dict

class MCP_ChatBot:

    def __init__(self):
        # Initialize session and client objects
        # self.sessions: List[ClientSession] = [] 
        self.exit_stack = AsyncExitStack() # new
        # self.anthropic = Anthropic()
        self.client = OpenAI(api_key=os.getenv("QWEN_API_KEY"),
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
        # self.available_tools: List[ToolDefinition] = [] # new
        self.tool_to_session: Dict[str, ClientSession] = {} # new
        self.model = "qwen-plus"
        self.available_tools = []
        self.available_prompts = []
        self.sessions = {}

    async def connect_to_server(self, server_name: str, server_config: dict) -> None:
        """Connect to a single MCP server."""
        try:
            server_params = StdioServerParameters(**server_config)
            stdio_transport = await self.exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            read, write = stdio_transport
            session = await self.exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            await session.initialize()

            try:
                # List available tools
                response = await session.list_tools()
                for tool in response.tools:
                    self.sessions[tool.name] = session
                    self.available_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": tool.inputSchema
                    }})
            
                # List available prompts
                prompts_response = await session.list_prompts()
                if prompts_response and prompts_response.prompts:
                    for prompt in prompts_response.prompts:
                        self.sessions[prompt.name] = session
                        self.available_prompts.append({
                        "type": "function",
                        "function": {
                            "name": prompt.name,
                            "description": prompt.description,
                            "arguments": prompt.arguments
                        }})
                # List available resources
                resources_response = await session.list_resources()
                if resources_response and resources_response.resources:
                    for resource in resources_response.resources:
                        resource_uri = str(resource.uri)
                        self.sessions[resource_uri] = session
            
            except Exception as e:
                print(f"Error {e}")
        except Exception as e:
            print(f"Failed to connect to {server_name}: {e}")

    async def connect_to_servers(self): # new
        """Connect to all configured MCP servers."""
        try:
            with open("server_config.json", "r") as file:
                data = json.load(file)
            
            servers = data.get("mcpServers", {})
            
            for server_name, server_config in servers.items():
                await self.connect_to_server(server_name, server_config)
        except Exception as e:
            print(f"Error loading server configuration: {e}")
            raise
    
    async def process_query(self, query):
        
        messages = [{'role':'user', 'content':query}]
        response = self.client.chat.completions.create(
                        model=self.model, 
                        messages=messages,
                        tools=self.available_tools)
        i = 1
        
        print(f"第{i}轮大模型输出信息：{response}\n")
        assistant_output = response.choices[0].message
        if assistant_output.content is None:
            assistant_output.content = ""
        messages.append(assistant_output)

        if assistant_output.tool_calls == None: 
            print(f"无需调用工具，我可以直接回复：{assistant_output.content}")
            return
        
        # 如果需要调用工具，则进行模型的多轮调用，直到模型判断无需调用工具
        while assistant_output.tool_calls != None:
            tool_info = {
                "content": "",
                "role": "tool",
                "tool_call_id": assistant_output.tool_calls[0].id,
            }

            tool_name = assistant_output.tool_calls[0].function.name
            tool_args = json.loads(assistant_output.tool_calls[0].function.arguments)
            
            print(f"模型决定调用工具: {tool_name}，参数: {tool_args}")
            
            session = self.sessions.get(tool_name)
            if not session:
                print(f"Tool '{tool_name}' not found.")
                break
            result = await session.call_tool(tool_name, arguments=tool_args)
                                
            tool_info["content"] = result.content
            tool_info["name"] = tool_name

            # print(f"工具 {tool_name} 执行结果: {result}")

            messages.append(tool_info)

            i += 1
            # print(f"第{i}轮大模型输入信息：{messages}\n")

            response = self.client.chat.completions.create(
                        model=self.model, 
                        messages=messages,
                        tools=self.available_tools)
            assistant_output = response.choices[0].message
            if assistant_output.content is None:
                assistant_output.content = ""
            messages.append(assistant_output)
            # print(f"第{i}轮大模型输出信息：{assistant_output}\n")

        # print(f"最终答案：{assistant_output.content}")

    async def get_resource(self, resource_uri):
        print(f"Fetching resource: {resource_uri}")
        print("Available sessions:", self.sessions.keys())
        session = self.sessions.get(resource_uri)
        
        # Fallback for papers URIs - try any papers resource session
        if not session and resource_uri.startswith("papers://"):
            for uri, sess in self.sessions.items():
                if uri.startswith("papers://"):
                    session = sess
                    break
            
        if not session:
            print(f"Resource '{resource_uri}' not found.")
            return
        
        try:
            result = await session.read_resource(uri=resource_uri)
            if result and result.contents:
                print(f"\nResource: {resource_uri}")
                print("Content:")
                print(result.contents[0].text)
            else:
                print("No content available.")
        except Exception as e:
            print(f"Error: {e}")
    
    async def list_prompts(self):
        """List all available prompts."""
        if not self.available_prompts:
            print("No prompts available.")
            return
        
        print("\nAvailable prompts:")
        for prompt in self.available_prompts:
            prompt = prompt['function']
            print(f"- {prompt['name']}: {prompt['description']}")
            if prompt['arguments']:
                print(f"  Arguments:")
                for arg in prompt['arguments']:
                    arg_name = arg.name if hasattr(arg, 'name') else arg.get('name', '')
                    print(f"    - {arg_name}")
    
    async def execute_prompt(self, prompt_name, args):
        """Execute a prompt with the given arguments."""
        session = self.sessions.get(prompt_name)
        if not session:
            print(f"Prompt '{prompt_name}' not found.")
            return
        
        try:
            result = await session.get_prompt(prompt_name, arguments=args)
            if result and result.messages:
                prompt_content = result.messages[0].content
                
                # Extract text from content (handles different formats)
                if isinstance(prompt_content, str):
                    text = prompt_content
                elif hasattr(prompt_content, 'text'):
                    text = prompt_content.text
                else:
                    # Handle list of content items
                    text = " ".join(item.text if hasattr(item, 'text') else str(item) 
                                  for item in prompt_content)
                
                print(f"\nExecuting prompt '{prompt_name}'...")
                print("Prompt content:")
                print(text)
                await self.process_query(text)
        except Exception as e:
            print(f"Error: {e}")

    async def chat_loop(self):
        print("\nMCP Chatbot Started!")
        print("Type your queries or 'quit' to exit.")
        print("Use @folders to see available topics")
        print("Use @<topic> to search papers in that topic")
        print("Use /prompts to list available prompts")
        print("Use /prompt <name> <arg1=value1> to execute a prompt")
        
        while True:
            try:
                query = input("\nQuery: ").strip()
                if not query:
                    continue
        
                if query.lower() == 'quit':
                    break
                
                # Check for @resource syntax first
                if query.startswith('@'):
                    # Remove @ sign  
                    topic = query[1:]
                    if topic == "folders":
                        resource_uri = "papers://folders"
                    else:
                        resource_uri = f"papers://{topic}"
                    await self.get_resource(resource_uri)
                    continue
                
                # Check for /command syntax
                if query.startswith('/'):
                    parts = query.split()
                    command = parts[0].lower()
                    
                    if command == '/prompts':
                        await self.list_prompts()
                    elif command == '/prompt':
                        if len(parts) < 2:
                            print("Usage: /prompt <name> <arg1=value1> <arg2=value2>")
                            continue
                        
                        prompt_name = parts[1]
                        args = {}
                        
                        # Parse arguments
                        for arg in parts[2:]:
                            if '=' in arg:
                                key, value = arg.split('=', 1)
                                args[key] = value
                        
                        await self.execute_prompt(prompt_name, args)
                    else:
                        print(f"Unknown command: {command}")
                    continue
                
                await self.process_query(query)
                    
            except Exception as e:
                print(f"\nError: {str(e)}")
    
    async def cleanup(self): # new
        """Cleanly close all resources using AsyncExitStack."""
        await self.exit_stack.aclose()

async def main():
    chatbot = MCP_ChatBot()
    try:
        # the mcp clients and sessions are not initialized using "with"
        # like in the previous lesson
        # so the cleanup should be manually handled
        await chatbot.connect_to_servers() # new! 
        await chatbot.chat_loop()
    finally:
        await chatbot.cleanup() #new! 

if __name__ == "__main__":
    asyncio.run(main())


# @folders
# @ai_interpretability
# /prompts
# /prompt generate_search_prompt topic=history num_papers=2