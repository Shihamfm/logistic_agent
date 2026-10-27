import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from typing import TypedDict, Annotated

from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.graph.message import add_messages
from langgraph.graph import StateGraph, START, END
from langchain_groq import ChatGroq
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver

load_dotenv()

# Setup and path resolution
script_dir = Path(__file__).resolve().parent
project_root = script_dir.parents[0]

if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.agent_tools import query_telemetry_db, fetch_corridor_conditions, search_compliance_sop

# llm
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

llm = ChatGroq(
    model='openai/gpt-oss-120b',
    api_key=GROQ_API_KEY
)

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

fde_tools = [query_telemetry_db, fetch_corridor_conditions, search_compliance_sop]
llm_with_tools = llm.bind_tools(fde_tools)


# Graph Architecture Assembly
def reasoning_node(state: AgentState):
    response = llm_with_tools.invoke(state['messages'])
    return {"messages": [response]}

print("--Compiling LangGraph FDE Orchestrator--")
graph_builder = StateGraph(AgentState)

graph_builder.add_edge(START, "reasoner")
graph_builder.add_node("reasoner", reasoning_node) # llm needs to make a decision or generate output
graph_builder.add_node("tools", ToolNode(fde_tools)) # inspect the latest message and passing it to a list of tools

graph_builder.add_conditional_edges("reasoner", tools_condition)
graph_builder.add_edge("tools", "reasoner")

fde_agent = graph_builder.compile(checkpointer=MemorySaver())

if __name__ == "__main__":
    prompt_path = project_root / "src" / "prompts" / "system_prompt.txt"

    try: 
        with open(prompt_path, "r", encoding="utf-8") as f:
            system_instruction = f.read()
    except FileNotFoundError:
        print(f"Couldn't find the {prompt_path}")
        system_instruction = "You are a helpful AI assistant"

    system_prompt = SystemMessage(content=system_instruction)

    thread_config = {
        "configurable": {"thread_id": "production_test"}
    }
    fde_agent.invoke(
        {"messages": [system_prompt]},
        config=thread_config
    )

    while True:
        user_input = input("\n Dispatcher: ")
        if user_input.lower() in ['exit', 'quit']:
            break

        events = fde_agent.stream(
            {"messages": [("user", user_input)]},
            config=thread_config,
            stream_mode="updates"
        )

        for event in events:
            for node_name, node_stat in event.items():
                if node_name == "tools":
                    print('Retrieving external data elements via ToolNode....')
                elif node_name == "reasoner":
                    latest_msg = node_stat["messages"][-1]
                    if latest_msg.content:
                        print(f"FDE Agent: \n {latest_msg.content}")
