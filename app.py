import streamlit as st
from agent import agent_app
from bc_integration import BusinessCentralAPI
from dotenv import load_dotenv
import uuid
import os
import requests 
import sqlite3
import pandas as pd
from langfuse.langchain import CallbackHandler
from langfuse import Langfuse

load_dotenv()

icon_path = "assets/d365businesscentral.png"
if not os.path.exists(icon_path) and os.path.exists("assets/d365businesscentral.svg"):
    icon_path = "assets/d365businesscentral.svg"

st.set_page_config(
    page_title="Manufacturing AI Agent", 
    layout="wide",
    page_icon=icon_path if os.path.exists(icon_path) else "⚙️"
)

# ---------------------------------------------------------
# PERSISTENT STORAGE (SQLite Database Setup & Migration)
# ---------------------------------------------------------
def init_db():
    conn = sqlite3.connect("orders_log.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS order_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_id TEXT,
                    product_code TEXT,
                    quantity INTEGER,
                    status TEXT,
                    triage_reason TEXT,
                    order_id TEXT,
                    email_content TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )''')
                
    try:
        c.execute("ALTER TABLE order_logs ADD COLUMN triage_category TEXT")
    except sqlite3.OperationalError:
        pass 

    conn.commit()
    conn.close()

def log_order(customer_id, product_code, quantity, status, triage_category, triage_reason, order_id, email_content):
    conn = sqlite3.connect("orders_log.db")
    c = conn.cursor()
    c.execute('''INSERT INTO order_logs 
                 (customer_id, product_code, quantity, status, triage_category, triage_reason, order_id, email_content) 
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', 
              (customer_id, product_code, quantity, status, triage_category, triage_reason, order_id, email_content))
    conn.commit()
    conn.close()

init_db()

# ---------------------------------------------------------
# UI HEADER WITH SEAMLESS LOGO ALIGNMENT
# ---------------------------------------------------------
# Using a tighter column ratio and vertical alignment to close the gap
col_icon, col_title = st.columns([1, 8], vertical_alignment="center")
with col_icon:
    if os.path.exists(icon_path):
        st.image(icon_path, use_container_width=True) # Makes the logo significantly larger and fills its column natively
with col_title:
    st.title("Agentic Order Processing Dashboard")

st.markdown("""
**An AI-driven Agentic workflow for automating unstructured order ingestion.** 
Powered by **LangGraph** and **LLMs**, this system dynamically extracts order parameters, performs real-time inventory and customer validation against **Microsoft Dynamics 365 Business Central**, and routes exceptions to a Human-in-the-Loop (HITL) triage interface. Full execution observability is maintained via **Langfuse** alongside a persistent SQLite audit log for central governance.
""")
st.divider()

# ---------------------------------------------------------
# SESSION STATE & LANGFUSE CALLBACK SETUP
# ---------------------------------------------------------
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
if "order_logged" not in st.session_state:
    st.session_state.order_logged = False

@st.cache_resource
def get_langfuse_handler():
    return CallbackHandler()

@st.cache_resource
def get_langfuse_client():
    return Langfuse()

langfuse_handler = get_langfuse_handler()
lf = get_langfuse_client()

config = {
    "configurable": {"thread_id": st.session_state.thread_id},
    "callbacks": [langfuse_handler]
}

@st.cache_data(ttl=300)
def load_bc_data():
    api = BusinessCentralAPI()
    try:
        customers = api.get_customers()
        items = api.get_items()
        return customers, items, None
    except Exception as e:
        return None, None, str(e)

with st.spinner("Connecting to Business Central APIs..."):
    customers, items, fetch_error = load_bc_data()

state = agent_app.get_state(config)

# ---------------------------------------------------------
# HELPER: VISUAL TRACE TIMELINE RENDERER (BLOCK FLOW)
# ---------------------------------------------------------
def render_trace_timeline(observations):
    st.markdown("### 🛤️ Execution Graph Flow")
    st.markdown("Click on any node block below to view its specific input, output, and latency metrics.")
    
    obs_list = []
    if isinstance(observations, list):
        obs_list = observations
    elif isinstance(observations, dict) and "data" in observations:
        obs_list = observations["data"]
        
    if not obs_list:
        st.info("No observation data found for this trace yet.")
        return
        
    try:
        obs_list = sorted(obs_list, key=lambda x: str(x.get("startTime", "") if isinstance(x, dict) else getattr(x, "startTime", "")))
    except Exception:
        pass
        
    for index, obs in enumerate(obs_list):
        item = obs.dict() if hasattr(obs, 'dict') else obs
        name = item.get("name", "Unknown Node")
        type_str = item.get("type", "UNKNOWN")
        latency = item.get("latency", 0)
        
        if type_str == "GENERATION":
            icon = "🧠"
        elif type_str == "CHAIN":
            icon = "⚙️" 
        else:
            icon = "⚡"
            
        if index > 0:
            st.markdown("<div style='text-align: left; padding-left: 20px; font-size: 24px; color: #888;'>⬇️</div>", unsafe_allow_html=True)
            
        with st.expander(f"{icon} **Node: {name}** | Type: `{type_str}` | Latency: {latency}s"):
            cols = st.columns(2)
            with cols[0]:
                st.markdown(f"**Start Time:** `{item.get('startTime')}`")
                st.markdown(f"**Observation ID:** `{item.get('id')}`")
            with cols[1]:
                st.markdown(f"**End Time:** `{item.get('endTime')}`")
                if item.get('parentObservationId'):
                    st.markdown(f"**Parent ID:** `{item.get('parentObservationId')}`")
            
            st.divider()
            st.markdown("**Raw Node Metadata (JSON)**")
            st.json(item)

# ---------------------------------------------------------
# TABS CONFIGURATION
# ---------------------------------------------------------
tab_workflow, tab_summary, tab_traceability, tab_manager_db = st.tabs([
    "🤖 Agent Workflow", 
    "📦 Processed Order Summary", 
    "🔍 Governance & Traceability",
    "🏛️ Global Order Governance & Audit" 
])

# =========================================================
# TAB 1: AGENT WORKFLOW
# =========================================================
with tab_workflow:
    # --- UI TRANSPARENCY DROPDOWN ---
    with st.expander("🔍 **System Transparency: APIs, Prompts & Architecture**", expanded=False):
        st.markdown("""
        **1. Agent Orchestration Workflow (LangGraph)**
        * `parse_email` ➔ `validate_order` ➔ (`human_triage` if discrepancy found) ➔ `execute_order`
        
        **2. LLM Instruction Prompt (Email Parser)**
        ```text
        Extract the exact order details from the following customer email.
        Email: {email_content}
        # The agent strictly formats the response using Pydantic fields: customer_id, product_code, quantity.
        ```
        
        **3. Microsoft Dynamics 365 Business Central API Calls**
        * **Authentication:** OAuth 2.0 via Microsoft Entra ID (MSAL)
        * **Inventory Check:** `GET /v2.0/{env}/api/v2.0/companies({id})/items?$filter=number eq '{code}'`
        * **Customer Check:** `GET /v2.0/{env}/api/v2.0/companies({id})/customers?$filter=number eq '{code}'`
        * **Order Execution:** `POST /v2.0/{env}/api/v2.0/companies({id})/salesOrders`
          * Payload Structure: `{"customerNumber": "...", "salesOrderLines": [{"itemId": "...", "quantity": ...}]}`
        """)

    if not state.values:
        st.subheader("1. Simulate Customer Email Request")
        if fetch_error:
            st.error("🚨 Connection to Business Central Failed")
            st.info(f"**Detailed Error Message:**\n\n`{fetch_error}`")
            st.stop()
            
        if customers and items:
            selected_customer = st.selectbox("Select Customer:", options=customers, format_func=lambda x: f"{x['displayName']} ({x['number']})")
            selected_item = st.selectbox("Select Requested Product:", options=items, format_func=lambda x: f"{x['displayName']} (Code: {x['number']}) - In Stock: {x.get('inventory', 0)}")
            selected_qty = st.number_input("Quantity:", min_value=1, value=5)

            default_email = f"Hello, I am {selected_customer['displayName']} (Account {selected_customer['number']}). Please create an order for {selected_qty} units of item {selected_item['number']}."
            
            st.divider()
            st.markdown("**Simulated Email Content (Editable for Multi-Triage Testing):**")
            st.info("💡 **Tip:** Edit the Account Number (e.g. 1000), Item Code, or Quantity in the text area below to test invalid customer, invalid product, or stock triage pathways.")
            editable_email = st.text_area("Incoming Customer Email", value=default_email, height=100, label_visibility="collapsed")

            if st.button("Submit Order to Agent", type="primary"):
                initial_state = {"email_content": editable_email}
                with st.spinner("Agent parsing email and validating against Business Central..."):
                    for event in agent_app.stream(initial_state, config=config):
                        pass 
                st.rerun() 

    elif state.next and "human_triage" in state.next:
        st.error(f"⚠️ Agent Paused: Triage Escalation Required [{state.values.get('triage_category', 'Validation Error')}]")
        
        triage_reason = state.values.get("triage_reason", "Validation failure detected.")
        st.warning(f"**Detailed Triage Cause:** {triage_reason}")
        
        st.write("**Extracted Order Details:**", state.values)
        st.subheader("Human Intervention Controls")
        
        col_c, col_p = st.columns(2)
        with col_c:
            override_cust = st.selectbox(
                "Fix / Select Valid Customer:", 
                options=[None] + customers, 
                format_func=lambda x: f"{x['displayName']} ({x['number']})" if x else "Keep Extracted Customer ID"
            )
        with col_p:
            override_item = st.selectbox(
                "Fix / Select Valid Replacement Product:", 
                options=[None] + items, 
                format_func=lambda x: f"{x['displayName']} (Code: {x['number']}) - In Stock: {x.get('inventory', 0)}" if x else "Keep Extracted Product Code"
            )
        
        col_act1, col_act2 = st.columns([2, 8])
        with col_act1:
            if st.button("Approve Overrides & Resume", type="primary"):
                updates = {}
                if override_cust:
                    updates["human_override_customer"] = override_cust['number']
                if override_item:
                    updates["human_override_code"] = override_item['number']
                    
                agent_app.update_state(config, updates)
                with st.spinner("Resuming execution to Business Central..."):
                    try:
                        for event in agent_app.stream(None, config=config):
                            pass
                    except Exception as e:
                        st.error(f"Failed to create order: {str(e)}")
                        st.stop()
                st.rerun()
                
        with col_act2:
            if st.button("Reject & Cancel Order"):
                log_order(
                    customer_id=state.values.get("customer_id", "N/A"),
                    product_code=state.values.get("product_code", "N/A"),
                    quantity=state.values.get("quantity", 0),
                    status="REJECTED",
                    triage_category=state.values.get("triage_category", "Rejected"),
                    triage_reason=triage_reason,
                    order_id="N/A",
                    email_content=state.values.get("email_content", "")
                )
                st.session_state.thread_id = str(uuid.uuid4())
                st.session_state.order_logged = False
                st.rerun()

    else:
        st.success("✅ Workflow Execution Finished. Order Processed Successfully!")
        st.info("👉 **Navigate to the 'Processed Order Summary' tab to view final details.**")
        
        if not st.session_state.order_logged:
            log_order(
                customer_id=state.values.get("human_override_customer") or state.values.get("customer_id", "N/A"),
                product_code=state.values.get("human_override_code") or state.values.get("product_code", "N/A"),
                quantity=state.values.get("quantity", 0),
                status="SUCCESS",
                triage_category=state.values.get("triage_category", "None"),
                triage_reason=state.values.get("triage_reason", "Passed validation"),
                order_id=state.values.get("order_id", "Error"),
                email_content=state.values.get("email_content", "")
            )
            st.session_state.order_logged = True
            
        if st.button("Start New Order"):
            st.session_state.thread_id = str(uuid.uuid4())
            st.session_state.order_logged = False
            st.rerun()

# =========================================================
# TAB 2: PROCESSED ORDER SUMMARY
# =========================================================
with tab_summary:
    if state.values and state.values.get("order_id"):
        st.subheader("Order Confirmation Details")
        col1, col2, col3 = st.columns(3)
        col1.metric("Final Customer ID", state.values.get("human_override_customer") or state.values.get("customer_id", "N/A"))
        col2.metric("Final Product Code", state.values.get("human_override_code") or state.values.get("product_code", "N/A"))
        col3.metric("Quantity Processed", state.values.get("quantity", 0))
        st.divider()
        col_left, col_right = st.columns(2)
        with col_left:
            st.markdown("### 📋 System Information")
            st.markdown(f"**Order ID (Business Central):** `{state.values.get('order_id')}`")
            st.markdown(f"**Validation Status:** `{state.values.get('validation_status')}`")
            if state.values.get("human_override_customer") or state.values.get("human_override_code"):
                st.warning("**Human Overrides Applied during Triage**")
        with col_right:
            st.markdown("### ✉️ Original Request")
            st.text_area("Email Content", value=state.values.get("email_content", ""), height=150, disabled=True)
    elif state.next and "human_triage" in state.next:
        st.warning("Order processing is currently paused for human triage. Check the Agent Workflow tab.")
    else:
        st.info("No completed orders yet. Start a new workflow in the 'Agent Workflow' tab.")

# =========================================================
# TAB 3: LANGFUSE GOVERNANCE & TRACEABILITY
# =========================================================
with tab_traceability:
    st.subheader("Observability & Auditing")
    st.markdown("All agent reasoning, LLM inputs/outputs, and tool invocations are actively monitored.")
    
    trace_id = getattr(langfuse_handler, "last_trace_id", None)
    if not trace_id and hasattr(langfuse_handler, "get_trace_id"):
        trace_id = langfuse_handler.get_trace_id()
    
    if trace_id:
        trace_url = lf.get_trace_url(trace_id=trace_id) if hasattr(lf, 'get_trace_url') else None
        
        if trace_url:
            st.success("✅ Active Trace Captured")
            st.markdown(f"**[🔗 Click Here to view full timeline & tree in Langfuse Dashboard]({trace_url})**")
            st.divider()
            
            if hasattr(lf, "flush"):
                lf.flush()
            
            trace_data = None
            
            try:
                if hasattr(lf, "api") and hasattr(lf.api, "observations"):
                    res = lf.api.observations.get_many(trace_id=trace_id)
                    trace_data = res.data if hasattr(res, 'data') else res
            except Exception:
                pass
                
            if not trace_data:
                try:
                    pk = os.getenv("LANGFUSE_PUBLIC_KEY")
                    sk = os.getenv("LANGFUSE_SECRET_KEY")
                    host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com").rstrip("/")
                    api_url = f"{host}/api/public/v2/observations?traceId={trace_id}"
                    resp = requests.get(api_url, auth=(pk, sk))
                    if resp.status_code == 200:
                        trace_data = resp.json()
                except Exception as api_e:
                    st.info("Syncing with Langfuse Cloud... Use the dashboard link above.")
            
            if trace_data:
                render_trace_timeline(trace_data)
                
    else:
        st.info("Waiting for agent execution to generate a trace URL. Run an order in the Workflow tab to see the link here.")
        
    st.divider()
    st.markdown(f"**Current LangGraph Thread ID:** `{st.session_state.thread_id}`")

# =========================================================
# TAB 4: GLOBAL ORDER GOVERNANCE & AUDIT 
# =========================================================
with tab_manager_db:
    st.subheader("Historical Order Analytics & Compliance")
    st.markdown("This governance center reads directly from the persistent `orders_log.db` SQLite database to track triage patterns and execution compliance.")
    
    try:
        conn = sqlite3.connect("orders_log.db")
        df = pd.read_sql_query("SELECT * FROM order_logs ORDER BY timestamp DESC", conn)
        conn.close()
        
        if not df.empty:
            m1, m2, m3 = st.columns(3)
            m1.metric("Total Requests Processed", len(df))
            m2.metric("Successful Orders", len(df[df['status'] == 'SUCCESS']))
            m3.metric("Rejected/Cancelled Orders", len(df[df['status'] == 'REJECTED']))
            
            st.divider()
            st.markdown("**Categorized Audit Log of Processed Orders:**")
            
            st.dataframe(
                df,
                use_container_width=True,
                column_config={
                    "id": "Log ID",
                    "status": st.column_config.TextColumn("Status"),
                    "triage_category": "Triage Category",
                    "triage_reason": "Detailed Reason",
                    "timestamp": st.column_config.DatetimeColumn("Execution Time", format="D MMM YYYY, h:mm a"),
                },
                hide_index=True
            )
        else:
            st.info("The database is currently empty. Run a workflow to populate records.")
    except Exception as db_e:
        st.error(f"Failed to read database: {str(db_e)}")