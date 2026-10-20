import os
import uuid
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

st.set_page_config(page_title="Stock Screener Agent", page_icon="📈")
st.title("📈 Stock Screener Agent")
st.info("Informational only, not financial advice. Data from Yahoo Finance.")
st.caption(
    "“Top mutual funds” means top mutual funds by today's % change, "
    "not fund quality or long-term returns."
)

try:
    api_key = st.secrets.get("GOOGLE_API_KEY")
except StreamlitSecretNotFoundError:
    api_key = None
api_key = api_key or os.environ.get("GOOGLE_API_KEY")

if not api_key:
    st.error("Add GOOGLE_API_KEY to Streamlit secrets or the environment to use the chat.")
    st.stop()

os.environ["GOOGLE_API_KEY"] = api_key
from stock_screener.flow import graph

MAX_USER_MESSAGES = 15
LIMIT_NOTICE = (
    "You've reached the 15-message limit for this chat. "
    "Select Clear chat to start a new conversation."
)


def safe(text: str) -> str:
    # Streamlit treats $...$ as LaTeX, which garbles dollar amounts like "$5.78T ... $62.47B"
    return text.replace("$", "\\$")


def is_quota_error(error: Exception) -> bool:
    current = error
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        status = getattr(current, "status_code", None) or getattr(current, "code", None)
        if str(status) == "429":
            return True
        message = str(current).upper()
        if any(marker in message for marker in ("RESOURCE_EXHAUSTED", "QUOTA EXCEEDED", "RATE LIMIT")):
            return True
        current = current.__cause__ or current.__context__
    return False


# Each browser session gets its own memory thread
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.history = []

user_message_count = sum(item["role"] == "user" for item in st.session_state.history)
limit_reached = user_message_count >= MAX_USER_MESSAGES
if limit_reached:
    st.info(LIMIT_NOTICE)

EXAMPLES = [
    "Top small cap tech gainers today",
    "Largest healthcare companies by market cap",
    "Tell me about NVDA",
    "Compare ZS and XP",
    "Show me the top mutual funds by today's % change",
]

with st.sidebar:
    st.header("Try asking")
    for example in EXAMPLES:
        if st.button(example, use_container_width=True, disabled=limit_reached):
            st.session_state.pending = example
    st.divider()
    if st.button("Clear chat", use_container_width=True):
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.history = []
        st.session_state.pop("pending", None)
        st.rerun()

# Show earlier turns
for item in st.session_state.history:
    with st.chat_message(item["role"]):
        st.markdown(safe(item["content"]))
        if item.get("tools"):
            with st.expander("Tools used"):
                for call in item["tools"]:
                    st.code(f"{call['name']}({call['args']})", language="python")

# New input: typed, or clicked from the sidebar
prompt = st.chat_input("Ask about stocks...", disabled=limit_reached)
if "pending" in st.session_state:
    prompt = st.session_state.pop("pending")

if prompt and not limit_reached:
    st.session_state.history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(safe(prompt))

    with st.chat_message("assistant"):
        try:
            with st.spinner("Thinking..."):
                result = graph.invoke(
                    {"messages": [{"role": "user", "content": prompt}]},
                    config={"configurable": {"thread_id": st.session_state.thread_id}},
                )
            msgs = result["messages"]
            last_human = max(i for i, m in enumerate(msgs) if m.type == "human")
            tools_used = [
                {"name": c["name"], "args": c["args"]}
                for m in msgs[last_human + 1:] if m.type == "ai"
                for c in (m.tool_calls or [])
            ]
            answer = msgs[-1].text
        except Exception as e:
            tools_used = []
            if is_quota_error(e):
                answer = "The AI service has reached its usage quota. Please try again later."
            else:
                answer = f"Something went wrong: {e}"

        st.markdown(safe(answer))
        if tools_used:
            with st.expander("Tools used"):
                for call in tools_used:
                    st.code(f"{call['name']}({call['args']})", language="python")

    st.session_state.history.append(
        {"role": "assistant", "content": answer, "tools": tools_used}
    )
    if user_message_count + 1 >= MAX_USER_MESSAGES:
        st.info(LIMIT_NOTICE)