import os
from typing import TypedDict
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_groq import ChatGroq
from langchain_core.prompts import PromptTemplate
from bc_integration import BusinessCentralAPI
from dotenv import load_dotenv

load_dotenv()
llm = ChatGroq(model_name="openai/gpt-oss-120b", temperature=0) 
bc_api = BusinessCentralAPI()

class OrderExtraction(BaseModel):
    customer_id: str = Field(description="The customer's ID number or account number")
    product_code: str = Field(description="The requested product or item code")
    quantity: int = Field(description="The number of units requested")

class AgentState(TypedDict):
    email_content: str
    customer_id: str
    product_code: str
    quantity: int
    validation_status: str
    triage_category: str
    triage_reason: str
    order_id: str
    human_override_customer: str
    human_override_code: str

def parse_email(state: AgentState):
    structured_llm = llm.with_structured_output(OrderExtraction)
    prompt = PromptTemplate.from_template(
        "Extract the exact order details from the following customer email.\nEmail: {email}"
    )
    chain = prompt | structured_llm
    result = chain.invoke({"email": state["email_content"]})
    
    return {
        "customer_id": str(result.customer_id).strip(),
        "product_code": str(result.product_code).strip(),
        "quantity": result.quantity
    }

def validate_order(state: AgentState):
    customer_to_check = state.get("human_override_customer") or state.get("customer_id")
    code_to_check = state.get("human_override_code") or state.get("product_code")
    requested_qty = state.get("quantity", 0)

    customer_data = bc_api.check_customer(customer_to_check)
    inventory_data = bc_api.check_inventory(code_to_check)

    reasons = []
    categories = []

    # 1. Customer Validation
    if not customer_data["exists"]:
        categories.append("Invalid / New Customer")
        reasons.append(f"Customer Account '{customer_to_check}' does not exist in Business Central.")

    # 2. Product Code Validation
    if not inventory_data["exists"]:
        categories.append("Invalid Product Code")
        reasons.append(f"Product Code '{code_to_check}' does not exist in inventory.")
    # 3. Inventory Stock Validation
    elif inventory_data["inventory"] < requested_qty:
        categories.append("Insufficient Inventory")
        reasons.append(
            f"Insufficient stock for '{code_to_check}'. Requested: {requested_qty}, Available: {inventory_data['inventory']}."
        )

    if categories:
        return {
            "validation_status": "FAILED",
            "triage_category": " & ".join(categories),
            "triage_reason": " | ".join(reasons)
        }

    return {
        "validation_status": "PASSED",
        "triage_category": "None",
        "triage_reason": "Order passed all customer, product, and stock checks."
    }

def route_order(state: AgentState):
    if state.get("validation_status") == "FAILED":
        return "human_triage"
    return "execute_order"

def human_triage(state: AgentState):
    pass

def execute_order(state: AgentState):
    # Safely extract the valid customer and code, defaulting to the original extraction if no override is present
    cust_to_use = state.get("human_override_customer") or state.get("customer_id")
    code_to_use = state.get("human_override_code") or state.get("product_code")
    
    result = bc_api.create_sales_order(cust_to_use, code_to_use, state["quantity"])
    return {"order_id": result.get("id", "Error")}

def build_graph():
    workflow = StateGraph(AgentState)
    workflow.add_node("parse", parse_email)
    workflow.add_node("validate", validate_order)
    workflow.add_node("human_triage", human_triage)
    workflow.add_node("execute_order", execute_order)

    workflow.set_entry_point("parse")
    workflow.add_edge("parse", "validate")
    workflow.add_conditional_edges("validate", route_order)
    workflow.add_edge("human_triage", "validate") 
    workflow.add_edge("execute_order", END)

    memory = MemorySaver()
    return workflow.compile(checkpointer=memory, interrupt_before=["human_triage"])

agent_app = build_graph()