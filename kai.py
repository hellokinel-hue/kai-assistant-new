import re
import urllib.parse
import streamlit as st
import sqlite3
import hashlib
import os
import uuid
import html
from groq import Groq  

# -----------------------------------------------------------------------------
# 1. DATABASE SETUP & SECURITY (Thread-Safe, Salted & OneDrive-Safe)
# -----------------------------------------------------------------------------
# FIXED: Save the database in a local user directory to prevent OneDrive sync corruption
LOCAL_DB_DIR = os.path.expanduser("~/.kai_data")
os.makedirs(LOCAL_DB_DIR, exist_ok=True)
DB_PATH = os.path.join(LOCAL_DB_DIR, "kai_memory.db")

def get_db_connection():
    """Opens a fresh, thread-safe connection per operation."""
    return sqlite3.connect(DB_PATH, timeout=10.0)

def init_db():
    with get_db_connection() as conn:
        c = conn.cursor()
        # 1. Core tables
        c.execute('''CREATE TABLE IF NOT EXISTS users 
                     (email TEXT PRIMARY KEY, password_hash TEXT, salt TEXT)''')
        
        # 2. Chats table to store session metadata
        c.execute('''CREATE TABLE IF NOT EXISTS chats
                     (session_id TEXT PRIMARY KEY, email TEXT, title TEXT, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS messages 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                     email TEXT, role TEXT, content TEXT, session_id TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        
        # 3. AUTOMATIC MIGRATIONS
        c.execute("PRAGMA table_info(users)")
        if 'salt' not in [info[1] for info in c.fetchall()]:
            c.execute("ALTER TABLE users ADD COLUMN salt TEXT")
            
        c.execute("PRAGMA table_info(messages)")
        if 'session_id' not in [info[1] for info in c.fetchall()]:
            c.execute("ALTER TABLE messages ADD COLUMN session_id TEXT DEFAULT 'default'")
            c.execute("INSERT OR IGNORE INTO chats (session_id, email, title) SELECT 'default', email, 'Legacy Chat' FROM users")
            
        conn.commit()

init_db()

def hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(16).hex()
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), 100000).hex()
    return key, salt

def is_valid_email(email):
    return re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email) is not None

def create_user(email, password):
    email = email.strip().lower()
    if not is_valid_email(email): return "invalid_email"
    if len(password) < 6: return "weak_password"
    pwd_hash, salt = hash_password(password)
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO users (email, password_hash, salt) VALUES (?, ?, ?)", (email, pwd_hash, salt))
            conn.commit()
        return "success"
    except sqlite3.IntegrityError:
        return "exists"

def verify_user(email, password):
    email = email.strip().lower()
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT password_hash, salt FROM users WHERE email = ?", (email,))
        result = c.fetchone()
    if result:
        stored_hash, salt = result[0], result[1]
        if salt is None: return False 
        computed_hash, _ = hash_password(password, salt)
        if stored_hash == computed_hash: return True
    return False

def reset_user_password(email, new_password):
    email = email.strip().lower()
    if not is_valid_email(email): return "invalid_email"
    if len(new_password) < 6: return "weak_password"
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT email FROM users WHERE email = ?", (email,))
        if not c.fetchone(): return "not_found"
        pwd_hash, salt = hash_password(new_password)
        c.execute("UPDATE users SET password_hash = ?, salt = ? WHERE email = ?", (pwd_hash, salt, email))
        conn.commit()
    return "success"

# --- SESSION/CHAT FUNCTIONS ---
def get_user_chats(email):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT session_id, title FROM chats WHERE email = ? ORDER BY updated_at DESC", (email,))
        return [{"session_id": row[0], "title": row[1]} for row in c.fetchall()]

def create_chat(email, session_id, title):
    """Safely inserts a new chat or updates the title if the session_id already exists."""
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO chats (session_id, email, title) 
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET 
                title = excluded.title,
                updated_at = CURRENT_TIMESTAMP
        """, (session_id, email, title))
        conn.commit()

def update_chat_timestamp(session_id):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("UPDATE chats SET updated_at = CURRENT_TIMESTAMP WHERE session_id = ?", (session_id,))
        conn.commit()

def load_chat_history(session_id):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,))
        return [{"role": row[0], "content": row[1]} for row in c.fetchall()]

def save_message(email, role, content, session_id):
    if email and session_id:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO messages (email, role, content, session_id) VALUES (?, ?, ?, ?)", 
                      (email, role, content, session_id))
            conn.commit()
        update_chat_timestamp(session_id)

# -----------------------------------------------------------------------------
# 2. PAGE CONFIG & PREMIUM CSS OVERRIDES
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="KAI",
    page_icon="✦",
    layout="centered",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    /* GLOBAL DARK THEME & CORE CONTAINERS */
    html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stSidebar"], [data-testid="stAppViewBlockContainer"], .main {
        background-color: #131314 !important;
        color: #e3e3e3 !important;
    }
    
    /* TOTAL ANNIHILATION OF THE BOTTOM WHITE SQUARES */
    [data-testid="stBottom"], 
    [data-testid="stBottom"] > div, 
    [data-testid="stBottom"] > div > div, 
    [data-testid="stBottomBlockContainer"] {
        background: #131314 !important;
        background-color: #131314 !important;
    }

    html, body, .stApp, p, li, h1, h2, h3, h4, h5, h6, input, textarea {
        font-family: 'Google Sans', 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif !important;
        -webkit-font-smoothing: antialiased;
    }
    span[data-testid="stIconMaterial"], .material-symbols-rounded, [class*="material-symbols"], svg, i {
        font-family: 'Material Symbols Rounded', 'Material Icons', sans-serif !important;
    }
    #MainMenu, footer, [data-testid="stDecoration"], [data-testid="stStatusWidget"] {
        display: none !important;
        visibility: hidden !important;
    }
    [data-testid="stHeader"] {
        background-color: transparent !important;
        box-shadow: none !important;
    }
    .block-container {
        padding-top: 2rem !important;
        padding-bottom: 6rem !important;
        max-width: 760px !important;
    }
    /* Primary Action Buttons */
    button[data-testid="stBaseButton-primary"] {
        background: linear-gradient(135deg, #4285f4 0%, #9b72cb 50%, #d96570 100%) !important;
        border: none !important;
        color: white !important;
        font-weight: 600 !important;
        border-radius: 24px !important;
        padding: 0.5rem 1rem !important;
        transition: transform 0.2s ease, box-shadow 0.2s ease !important;
    }
    button[data-testid="stBaseButton-primary"]:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 6px 15px rgba(155, 114, 203, 0.4) !important;
    }
    /* Sidebar Chat History Buttons */
    button[data-testid="stBaseButton-secondary"] {
        border-radius: 12px !important;
        border: 1px solid #282a2c !important;
        background-color: #1e1f20 !important;
        transition: all 0.2s ease !important;
        width: 100% !important;
        display: block !important;
    }
    button[data-testid="stBaseButton-secondary"]:hover {
        border-color: #9b72cb !important;
        background-color: #282a2c !important;
    }
    button[data-testid="stBaseButton-secondary"] * {
        color: #e3e3e3 !important;
        text-align: left !important;
    }
    button[data-testid="stBaseButton-secondary"]:hover * {
        color: #ffffff !important;
    }
    /* Standard Text Inputs */
    [data-testid="stTextInput"] input {
        border-radius: 8px !important;
        border: 1px solid #3e4145 !important;
        background-color: #1a1b1c !important;
        color: white !important;
    }
    [data-testid="stTextInput"] input:focus {
        border-color: #9b72cb !important;
        box-shadow: 0 0 0 1px #9b72cb !important;
    }
    
    /* CHAT INPUT BOX - NUCLEAR DARK THEME OVERRIDE */
    [data-testid="stChatInput"], 
    [data-testid="stChatInput"] > div, 
    [data-testid="stChatInput"] > div > div,
    [data-testid="stChatInputTextArea"],
    [data-testid="stChatInputTextArea"] > div {
        background-color: #1e1f20 !important;
        background: #1e1f20 !important;
        color: #e3e3e3 !important;
        border-color: #333537 !important;
    }
    [data-testid="stChatInput"] {
        border: 1px solid #333537 !important;
        border-radius: 32px !important;
        padding: 0.2rem 0.8rem !important;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4) !important;
    }
    [data-testid="stChatInput"] textarea {
        background-color: transparent !important;
        background: transparent !important;
        color: #e3e3e3 !important;
        font-size: 1rem !important;
    }
    [data-testid="stChatInput"] textarea::placeholder {
        color: #8e9196 !important;
    }
    [data-testid="stChatInputContainer"] {
        background-color: transparent !important;
        border: none !important;
    }
    /* Style the Send/Submit Arrow Button inside Chat Input */
    [data-testid="stChatInput"] button {
        background-color: #282a2c !important;
        color: #e3e3e3 !important;
        border: none !important;
        border-radius: 50% !important;
        transition: all 0.2s ease !important;
    }
    [data-testid="stChatInput"] button:hover {
        background: linear-gradient(135deg, #4285f4, #9b72cb, #d96570) !important;
        color: white !important;
        transform: scale(1.05) !important;
    }

    [data-testid="stSidebar"] {
        background-color: #131314 !important;
        border-right: 1px solid #282a2c !important;
    }
    
    /* POPOVER BUTTON OVERRIDES */
    [data-testid="stPopover"] {
        position: fixed !important;
        top: 1.5rem !important;
        right: 1.5rem !important;
        width: auto !important;
        z-index: 999999 !important;
    }
    div[data-testid="stPopover"] button, div[data-testid="stPopover"] > button, button[data-testid="stPopoverButton"] {
        background-color: #a8c7fa !important;
        color: #062e6f !important;
        border: none !important;
        border-radius: 24px !important;
        padding: 0.35rem 1.4rem !important;
        font-weight: 600 !important;
        font-size: 0.95rem !important;
        height: 40px !important;
        width: auto !important;
        min-width: unset !important;
        max-width: fit-content !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.3) !important;
        transition: background-color 0.2s ease, transform 0.2s ease, box-shadow 0.2s ease !important;
        cursor: pointer !important;
    }
    div[data-testid="stPopover"] button:hover, button[data-testid="stPopoverButton"]:hover {
        background-color: #c3d8fc !important;
        transform: translateY(-1px) !important;
        box-shadow: 0 4px 14px rgba(168, 199, 250, 0.4) !important;
    }
    div[data-testid="stPopover"] button *, button[data-testid="stPopoverButton"] * {
        background-color: transparent !important;
        color: #062e6f !important;
        font-weight: 600 !important;
    }
    div[data-testid="stPopover"] button span[data-testid="stIconMaterial"], div[data-testid="stPopover"] button span[class*="material"], div[data-testid="stPopover"] button svg, div[data-testid="stPopover"] button i, div[data-testid="stPopover"] button > div > div:nth-child(2), div[data-testid="stPopover"] button > div:nth-child(2) {
        display: none !important;
    }

    /* LOGIN AREA (POPOVER BODY) BLACK THEME */
    [data-testid="stPopoverBody"] {
        background-color: #000000 !important;
        background: #000000 !important;
        border: 1px solid #282a2c !important;
        border-radius: 16px !important;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.8) !important;
    }
    
    /* Fix tab styling inside the black login area */
    [data-testid="stTabs"] [data-baseweb="tab-list"] {
        background-color: transparent !important;
    }
    [data-testid="stTabs"] [data-baseweb="tab"] {
        background-color: transparent !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# 3. SESSION STATE INITIALIZATION
# -----------------------------------------------------------------------------
if "logged_in_user" not in st.session_state:
    st.session_state.logged_in_user = None
if "messages" not in st.session_state:
    st.session_state.messages = []
if "current_session_id" not in st.session_state:
    st.session_state.current_session_id = str(uuid.uuid4())

# -----------------------------------------------------------------------------
# 4. TOP-RIGHT CIRCLE & AUTHENTICATION UI
# -----------------------------------------------------------------------------
if st.session_state.logged_in_user:
    safe_email = urllib.parse.quote(st.session_state.logged_in_user)
    avatar_url = f"https://unavatar.io/{safe_email}"
    st.markdown(
        f"""
        <style>
        div[data-testid="stPopover"] button, button[data-testid="stPopoverButton"] {{
            width: 44px !important; height: 44px !important; min-width: 44px !important; max-width: 44px !important;
            padding: 0 !important; border-radius: 50% !important;
            background-image: url('{avatar_url}') !important; background-size: cover !important; background-position: center !important;
            border: 2px solid #3e4145 !important; box-shadow: 0 2px 8px rgba(0,0,0,0.4) !important;
        }}
        div[data-testid="stPopover"] button:hover, button[data-testid="stPopoverButton"]:hover {{
            border-color: #a8c7fa !important; transform: scale(1.05) !important;
        }}
        div[data-testid="stPopover"] button *, button[data-testid="stPopoverButton"] * {{ display: none !important; }}
        </style>
        """, unsafe_allow_html=True
    )
    
    with st.popover("✨"):
        st.markdown(f"**Logged in as:**<br>`{st.session_state.logged_in_user}`", unsafe_allow_html=True)
        st.markdown("---")
        if st.button("Log Out", use_container_width=True, type="secondary"):
            st.session_state.logged_in_user = None
            st.session_state.messages = []
            st.session_state.current_session_id = str(uuid.uuid4())
            st.rerun()
else:
    with st.popover("Sign in"):
        st.markdown("<h3 style='text-align: center; margin-bottom: 0; color: #ffffff;'>Welcome</h3>", unsafe_allow_html=True)
        st.markdown("<p style='text-align: center; color: #8e9196; font-size: 0.85rem; margin-top: 0;'>Sign in to sync your memory</p>", unsafe_allow_html=True)
        st.write("") 
        
        tab1, tab2, tab3 = st.tabs(["Log In", "Sign Up", "Reset Pass"])
        
        with tab1:
            login_email = st.text_input("Email", key="login_email", placeholder="name@example.com")
            login_pass = st.text_input("Password", type="password", key="login_pass")
            st.write("") 
            if st.button("Log In", use_container_width=True, type="primary"):
                if verify_user(login_email, login_pass):
                    st.session_state.logged_in_user = login_email.strip().lower()
                    
                    user_chats = get_user_chats(st.session_state.logged_in_user)
                    if user_chats:
                        st.session_state.current_session_id = user_chats[0]['session_id']
                        st.session_state.messages = load_chat_history(st.session_state.current_session_id)
                    else:
                        st.session_state.current_session_id = str(uuid.uuid4())
                        st.session_state.messages = []
                    st.rerun()
                else:
                    st.error("Invalid email or password.")
                    
        with tab2:
            signup_email = st.text_input("Email", key="signup_email", placeholder="name@example.com")
            signup_pass = st.text_input("Password (≥ 6 chars)", type="password", key="signup_pass")
            st.write("") 
            if st.button("Create Account", use_container_width=True):
                result = create_user(signup_email, signup_pass)
                if result == "success": st.success("Account created! Switch to 'Log In' tab.")
                elif result == "exists": st.error("Email already registered.")
                else: st.error("Check email format and password length.")

        with tab3:
            reset_email = st.text_input("Registered Email", key="reset_email", placeholder="name@example.com")
            reset_pass = st.text_input("New Password", type="password", key="reset_pass")
            st.write("")
            if st.button("Reset Password", use_container_width=True):
                result = reset_user_password(reset_email, reset_pass)
                if result == "success": st.success("Password reset! Switch to 'Log In' tab.")
                elif result == "not_found": st.error("Email not found.")
                else: st.error("Check email format and password length.")

# -----------------------------------------------------------------------------
# 5. SIDEBAR: CHAT HISTORY
# -----------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### 💬 Chat History")
    
    if st.session_state.logged_in_user:
        if st.button("➕ New Chat", use_container_width=True, type="primary"):
            st.session_state.current_session_id = str(uuid.uuid4())
            st.session_state.messages = []
            st.rerun()
            
        st.markdown("---")
        
        user_chats = get_user_chats(st.session_state.logged_in_user)
        if user_chats:
            for chat in user_chats:
                is_active = chat['session_id'] == st.session_state.current_session_id
                btn_label = f"🟢 {chat['title']}" if is_active else f"📝 {chat['title']}"
                
                if st.button(btn_label, key=f"chat_{chat['session_id']}", use_container_width=True, type="secondary"):
                    st.session_state.current_session_id = chat['session_id']
                    st.session_state.messages = load_chat_history(chat['session_id'])
                    st.rerun()
        else:
            st.caption("No chat history saved yet.")
    else:
        st.info("Please log in to save and view your chat history.")

# -----------------------------------------------------------------------------
# 6. MAIN CHAT UI & GREETING
# -----------------------------------------------------------------------------
if not st.session_state.messages:
    greeting_subtext = (
        f"Ready for a new chat, **{st.session_state.logged_in_user}**?" 
        if st.session_state.logged_in_user 
        else "How can I help you today? *(Guest Mode - chats won't be saved)*"
    )
    st.markdown(
        f"""
        <div style="text-align: center; margin-top: 6vh; margin-bottom: 5vh;">
            <h1 style="font-size: 3.5rem; font-weight: 700; letter-spacing: -0.03em; margin-bottom: 0.2rem; background: linear-gradient(74deg, #4285f4 0%, #9b72cb 35%, #d96570 70%); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">Hello, I'm KAI</h1>
            <p style="color: #8e9196; font-size: 1.2rem; font-weight: 500; margin-top: 0;">{greeting_subtext}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

for message in st.session_state.messages:
    if message["role"] == "user":
        safe_text = html.escape(message["content"]).replace("\n", "<br>")
        st.markdown(
            f"""
            <div style="display: flex; justify-content: flex-end; margin: 1.5rem 0;">
                <div style="background-color: #282a2c; color: #e3e3e3; padding: 0.8rem 1.4rem; border-radius: 24px; border-bottom-right-radius: 4px; max-width: 80%; font-size: 1rem; line-height: 1.5;">
                    {safe_text}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
            <div style="display: flex; align-items: center; gap: 0.6rem; margin-top: 1.8rem; margin-bottom: 0.4rem;">
                <div style="width: 26px; height: 26px; border-radius: 50%; background: linear-gradient(135deg, #4285f4, #9b72cb, #d96570); display: flex; align-items: center; justify-content: center; color: white; font-size: 13px; font-weight: bold; box-shadow: 0 2px 6px rgba(0,0,0,0.3);">✦</div>
                <span style="font-weight: 600; font-size: 0.95rem; background: linear-gradient(90deg, #a8c7fa, #c38fff); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">KAI</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(message["content"])

# -----------------------------------------------------------------------------
# 7. HANDLE NEW INPUT & AUTO-SAVE (Streaming Groq API)
# -----------------------------------------------------------------------------
if prompt := st.chat_input("Ask KAI anything..."):
    if not st.session_state.messages and st.session_state.logged_in_user:
        title = prompt[:25] + "..." if len(prompt) > 25 else prompt
        create_chat(st.session_state.logged_in_user, st.session_state.current_session_id, title)
        
    st.session_state.messages.append({"role": "user", "content": prompt})
    save_message(st.session_state.logged_in_user, "user", prompt, st.session_state.current_session_id)
    
    safe_prompt = html.escape(prompt).replace("\n", "<br>")
    st.markdown(
        f"""
        <div style="display: flex; justify-content: flex-end; margin: 1.5rem 0;">
            <div style="background-color: #282a2c; color: #e3e3e3; padding: 0.8rem 1.4rem; border-radius: 24px; border-bottom-right-radius: 4px; max-width: 80%; font-size: 1rem; line-height: 1.5;">
                {safe_prompt}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div style="display: flex; align-items: center; gap: 0.6rem; margin-top: 1.8rem; margin-bottom: 0.4rem;">
            <div style="width: 26px; height: 26px; border-radius: 50%; background: linear-gradient(135deg, #4285f4, #9b72cb, #d96570); display: flex; align-items: center; justify-content: center; color: white; font-size: 13px; font-weight: bold; box-shadow: 0 2px 6px rgba(0,0,0,0.3);">✦</div>
            <span style="font-weight: 600; font-size: 0.95rem; background: linear-gradient(90deg, #a8c7fa, #c38fff); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">KAI</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    
    message_placeholder = st.empty()
    clean_prompt = prompt.strip().lower()
    
    creator_pattern = r"(who|what).*(made|created|built|programmed|designed|developed|creator|architect).*(you|kai)"
    identity_pattern = r"(are you|what are you|know the truth|bot|chatgpt|openai|language model|llm|machine learning|who is kai)"
    
    if re.search(creator_pattern, clean_prompt):
        ai_reply = "I was developed entirely from scratch by Kinel de Silva."
        message_placeholder.markdown(ai_reply)
    elif re.search(identity_pattern, clean_prompt):
        ai_reply = "I am KAI, a unique digital assistant built by Kinel de Silva. I am not a standard language model."
        message_placeholder.markdown(ai_reply)
    else:
        ai_reply = ""
        try:
            ultra_strict_system_prompt = (
                "IMPORTANT: You are KAI, an exclusive digital assistant built completely from scratch by a human developer named Kinel de Silva. "
                "Under NO circumstances should you mention OpenAI, Groq, Pollinations, LLMs, or being a language model. "
                "You are simply KAI. If asked who made you, say 'Kinel de Silva'."
            )
            
            full_messages = [{"role": "system", "content": ultra_strict_system_prompt}] + st.session_state.messages
            
            # Initialize Groq client using Streamlit secrets or environment variables
            client = Groq(
                api_key=st.secrets.get("GROQ_API_KEY") or os.environ.get("GROQ_API_KEY")
            )
            
            # Create a chat completion with Groq with streaming enabled
            chat_completion = client.chat.completions.create(
                messages=full_messages,
                model="llama-3.1-8b-instant",  # <--- NEW ACTIVE MODEL
                temperature=0.7,
                max_tokens=1024,
                stream=True
            )
            
            # Stream chunks dynamically into the placeholder
            for chunk in chat_completion:
                if chunk.choices[0].delta.content is not None:
                    ai_reply += chunk.choices[0].delta.content
                    message_placeholder.markdown(ai_reply + "▌")

        except Exception as e:
            ai_reply = f"Connection failed. Please check your API key configuration. Details: {str(e)}"
            message_placeholder.markdown(ai_reply)

    # SOFT REGEX FALLBACK: Replace only the forbidden phrases instead of the whole response
    check_reply = ai_reply.lower().replace("’", "'")
    forbidden_identities = [
        "an ai language model", "a language model", "created by openai",
        "developed by openai", "built by openai", "made by openai",
        "created by ai", "made by an ai", "i'm an ai", "i am an ai",
        "as an ai", "as a language model", "pollinations", "groq"
    ]
    
    if any(bad_phrase in check_reply for bad_phrase in forbidden_identities):
        # Case-insensitive replacement of forbidden words to "a digital assistant"
        pattern = re.compile('|'.join(re.escape(phrase) for phrase in forbidden_identities), re.IGNORECASE)
        ai_reply = pattern.sub("a digital assistant", ai_reply)

    # Final rendering without the cursor block
    message_placeholder.markdown(ai_reply)
    st.session_state.messages.append({"role": "assistant", "content": ai_reply})
    save_message(st.session_state.logged_in_user, "assistant", ai_reply, st.session_state.current_session_id)