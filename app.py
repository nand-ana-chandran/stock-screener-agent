import uuid
import streamlit as st
from flow import graph

st.set_page_config(page_title="Stock Screener Agent", page_icon="📈")
st.title("📈 Stock Screener Agent")
st.caption("Data from Yahoo Finance. Informational only, not financial advice.")
st.caption(
    "“Top mutual funds” means top mutual funds by today's % change, "
    "not fund quality or long-term returns."
)


def safe(text: str) -> str:
    # Streamlit treats $...$ as LaTeX, which garbles dollar amounts like "$5.78T ... $62.47B"
    return text.replace("$", "\\$")


# Each browser session gets its own memory thread
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.history = []

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
        if st.button(example, use_container_width=True):
            st.session_state.pending = example
    st.divider()
    if st.button("New chat", use_container_width=True):
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.history = []
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
prompt = st.chat_input("Ask about stocks...")
if "pending" in st.session_state:
    prompt = st.session_state.pop("pending")

if prompt:
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
            tools_used, answer = [], f"Something went wrong: {e}"

        st.markdown(safe(answer))
        if tools_used:
            with st.expander("Tools used"):
                for call in tools_used:
                    st.code(f"{call['name']}({call['args']})", language="python")

    st.session_state.history.append(
        {"role": "assistant", "content": answer, "tools": tools_used}
    )