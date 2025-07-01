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
        self.sessions: List[ClientSession] = [] # new
        self.exit_stack = AsyncExitStack() # new
        # self.anthropic = Anthropic()
        self.client = OpenAI(api_key=os.getenv("QWEN_API_KEY"),
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.available_tools: List[ToolDefinition] = [] # new
        self.tool_to_session: Dict[str, ClientSession] = {} # new
        self.model = "qwen-plus"

    async def connect_to_server(self, server_name: str, server_config: dict) -> None:
        """Connect to a single MCP server."""
        try:
            server_params = StdioServerParameters(**server_config)
            stdio_transport = await self.exit_stack.enter_async_context(
                stdio_client(server_params)
            ) # new
            read, write = stdio_transport
            session = await self.exit_stack.enter_async_context(
                ClientSession(read, write)
            ) # new
            await session.initialize()
            self.sessions.append(session)
            
            # List available tools for this session
            response = await session.list_tools()
            tools = response.tools
            print(f"\nConnected to {server_name} with tools:", [t.name for t in tools])
            
            for tool in tools: # new
                self.tool_to_session[tool.name] = session
                self.available_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": tool.inputSchema
                    }})
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
            
            session = self.tool_to_session[tool_name] # new
            result = await session.call_tool(tool_name, arguments=tool_args)
                                
            tool_info["content"] = result.content
            tool_info["name"] = tool_name

            print(f"工具 {tool_name} 执行结果: {result}")

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
            print(f"第{i}轮大模型输出信息：{assistant_output}\n")

        print(f"最终答案：{assistant_output.content}")

    async def chat_loop(self):
        """Run an interactive chat loop"""
        print("\nMCP Chatbot Started!")
        print("Type your queries or 'quit' to exit.")
        
        while True:
            try:
                query = input("\nQuery: ").strip()
        
                if query.lower() == 'quit':
                    break
                    
                await self.process_query(query)
                print("\n")
                    
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


## Fetch the content of this website: https://modelcontextprotocol.io/docs/concepts/architecture and save the content in the file "mcp_summary.md", create a visual diagram that summarizes the content of "mcp_summary.md" and save it in a text file
## Fetch deeplearning.ai and find an interesting term. Search for 2 papers around the term and then summarize your findings and write them to a file called results.txt