import re
import urllib.parse
import streamlit as st
import sqlite3
import hashlib
import os
import uuid
import html
import base64
import io
import requests
from PIL import Image, ImageDraw, ImageFont
from groq import Groq  

# -----------------------------------------------------------------------------
# 1. DATABASE SETUP & SECURITY
# -----------------------------------------------------------------------------
LOCAL_DB_DIR = os.path.expanduser("~/.kai_data")
os.makedirs(LOCAL_DB_DIR, exist_ok=True)
DB_PATH = os.path.join(LOCAL_DB_DIR, "kai_memory.db")

def get_db_connection():
    return sqlite3.connect(DB_PATH, timeout=10.0)

def init_db():
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users 
                     (email TEXT PRIMARY KEY, password_hash TEXT, salt TEXT)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS chats
                     (session_id TEXT PRIMARY KEY, email TEXT, title TEXT, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS messages 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                     email TEXT, role TEXT, content TEXT, session_id TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        
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

def get_user_chats(email):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT session_id, title FROM chats WHERE email = ? ORDER BY updated_at DESC", (email,))
        return [{"session_id": row[0], "title": row[1]} for row in c.fetchall()]

def create_chat(email, session_id, title):
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
# 2. PAGE CONFIG & AGGRESSIVE CSS OVERRIDES
# -----------------------------------------------------------------------------
st.set_page_config(page_title="KAI", page_icon="✦", layout="centered", initial_sidebar_state="expanded")

st.markdown(
    """
    <style>
    html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stSidebar"], [data-testid="stAppViewBlockContainer"], .main {
        background-color: #131314 !important; color: #e3e3e3 !important;
    }
    [data-testid="stBottom"], [data-testid="stBottom"] > div, [data-testid="stBottom"] > div > div, [data-testid="stBottomBlockContainer"] {
        background: #131314 !important; background-color: #131314 !important;
    }
    html, body, .stApp, p, li, h1, h2, h3, h4, h5, h6, input, textarea {
        font-family: 'Google Sans', 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif !important; -webkit-font-smoothing: antialiased;
    }
    #MainMenu, footer, [data-testid="stDecoration"], [data-testid="stStatusWidget"] {
        display: none !important; visibility: hidden !important;
    }
    [data-testid="stHeader"] { background-color: transparent !important; box-shadow: none !important; }
    .block-container { padding-top: 2rem !important; padding-bottom: 6rem !important; max-width: 760px !important; }
    
    button[data-testid="stBaseButton-primary"] {
        background: linear-gradient(135deg, #4285f4 0%, #9b72cb 50%, #d96570 100%) !important;
        border: none !important; color: white !important; font-weight: 600 !important;
        border-radius: 24px !important; padding: 0.5rem 1rem !important;
        transition: transform 0.2s ease, box-shadow 0.2s ease !important;
    }
    button[data-testid="stBaseButton-primary"]:hover {
        transform: translateY(-2px) !important; box-shadow: 0 6px 15px rgba(155, 114, 203, 0.4) !important;
    }
    
    button[data-testid="stBaseButton-secondary"] {
        border-radius: 12px !important; border: 1px solid #282a2c !important; background-color: #1e1f20 !important;
        transition: all 0.2s ease !important; width: 100% !important; display: block !important;
    }
    button[data-testid="stBaseButton-secondary"]:hover {
        border-color: #9b72cb !important; background-color: #282a2c !important;
    }
    
    [data-testid="stTextInput"] input {
        border-radius: 8px !important; border: 1px solid #3e4145 !important;
        background-color: #1a1b1c !important; color: white !important;
    }
    
    /* CHAT INPUT STYLING */
    [data-testid="stChatInput"] {
        background-color: #1e1f20 !important; border: 1px solid #333537 !important;
        border-radius: 32px !important; padding: 0.2rem 0.8rem 0.2rem 3.5rem !important; 
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4) !important;
    }
    [data-testid="stChatInput"] * {
        background-color: transparent !important; color: #e3e3e3 !important;
    }
    [data-testid="stChatInput"] button {
        background-color: #282a2c !important; color: #e3e3e3 !important; border: none !important;
        border-radius: 50% !important; transition: all 0.2s ease !important;
    }
    [data-testid="stChatInput"] button:hover {
        background: linear-gradient(135deg, #4285f4, #9b72cb, #d96570) !important;
        color: white !important; transform: scale(1.05) !important;
    }

    [data-testid="stSidebar"] { background-color: #131314 !important; border-right: 1px solid #282a2c !important; }
    
    /* POPOVER STYLES */
    div[data-testid="stPopover"] button {
        background-color: #1e1f20 !important; color: #e3e3e3 !important; border: 1px solid #333537 !important;
        border-radius: 24px !important; padding: 0.35rem 1.2rem !important; font-weight: 600 !important;
        transition: all 0.2s ease !important;
    }
    div[data-testid="stPopover"] button:hover {
        background-color: #282a2c !important; border-color: #9b72cb !important;
    }
    [data-testid="stPopoverBody"] {
        background-color: #000000 !important; background: #000000 !important; border: 1px solid #282a2c !important;
        border-radius: 16px !important; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.8) !important;
    }
    
    /* TOP-RIGHT PROFILE / SIGN IN */
    .st-key-profile_popover, .st-key-login_popover {
        position: fixed !important; top: 1.2rem !important; right: 1.5rem !important; z-index: 999999 !important;
    }
    
    /* BULLETPROOF ATTACH (+) BUTTON POSITIONING */
    .st-key-attach_popover {
        position: fixed !important; 
        bottom: 27px !important; 
        left: 50% !important; 
        margin-left: -375px !important;
        z-index: 9999999 !important;
    }
    
    @media (max-width: 780px) {
        .st-key-attach_popover {
            left: 20px !important;
            margin-left: 0 !important;
        }
    }

    .st-key-attach_popover button {
        background: transparent !important; color: #8e9196 !important; border: none !important;
        width: 40px !important; height: 40px !important; border-radius: 50% !important;
        font-size: 1.4rem !important; padding: 0 !important; box-shadow: none !important;
        display: flex !important; align-items: center !important; justify-content: center !important;
    }
    .st-key-attach_popover button:hover {
        background-color: #333537 !important; color: #e3e3e3 !important; border-color: transparent !important;
    }

    /* CENTERED RADIO BUTTONS FOR MODEL TOGGLE */
    .stRadio > div { justify-content: center !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# 3. SESSION STATE INITIALIZATION
# -----------------------------------------------------------------------------
if "logged_in_user" not in st.session_state: st.session_state.logged_in_user = None
if "messages" not in st.session_state: st.session_state.messages = []
if "current_session_id" not in st.session_state: st.session_state.current_session_id = str(uuid.uuid4())
if "kai_mode" not in st.session_state: st.session_state.kai_mode = "🧠 Pro"

# -----------------------------------------------------------------------------
# 4. TOP-RIGHT AUTHENTICATION / PROFILE
# -----------------------------------------------------------------------------
if st.session_state.logged_in_user:
    safe_email = urllib.parse.quote(st.session_state.logged_in_user)
    avatar_url = f"https://unavatar.io/{safe_email}"
    
    st.markdown(
        f"""
        <style>
        .st-key-profile_popover button {{
            width: 42px !important; height: 42px !important; min-width: 42px !important; max-width: 42px !important;
            padding: 0 !important; border-radius: 50% !important;
            background-image: url('{avatar_url}') !important; background-size: cover !important; background-position: center !important;
            border: 2px solid #3e4145 !important; box-shadow: 0 2px 8px rgba(0,0,0,0.4) !important;
        }}
        .st-key-profile_popover button:hover {{ border-color: #a8c7fa !important; transform: scale(1.05) !important; }}
        .st-key-profile_popover button * {{ display: none !important; }}
        </style>
        """, unsafe_allow_html=True
    )
    
    with st.container():
        with st.popover("Profile", key="profile_popover"):
            st.markdown(f"**Logged in as:**<br>`{st.session_state.logged_in_user}`", unsafe_allow_html=True)
            st.markdown("---")
            if st.button("Log Out", use_container_width=True, type="secondary"):
                st.session_state.logged_in_user = None
                st.session_state.messages = []
                st.session_state.current_session_id = str(uuid.uuid4())
                st.rerun()
else:
    with st.container():
        with st.popover("Sign in", key="login_popover"):
            st.markdown("<h3 style='text-align: center; margin-bottom: 0; color: #ffffff;'>Welcome</h3>", unsafe_allow_html=True)
            tab1, tab2, tab3 = st.tabs(["Log In", "Sign Up", "Reset Pass"])
            
            with tab1:
                login_email = st.text_input("Email", key="login_email", placeholder="name@example.com")
                login_pass = st.text_input("Password", type="password", key="login_pass")
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
                if st.button("Create Account", use_container_width=True):
                    result = create_user(signup_email, signup_pass)
                    if result == "success": st.success("Account created! Switch to 'Log In' tab.")
                    elif result == "exists": st.error("Email already registered.")
                    else: st.error("Check email format and password length.")

            with tab3:
                reset_email = st.text_input("Registered Email", key="reset_email", placeholder="name@example.com")
                reset_pass = st.text_input("New Password", type="password", key="reset_pass")
                if st.button("Reset Password", use_container_width=True):
                    result = reset_user_password(reset_email, reset_pass)
                    if result == "success": st.success("Password reset! Switch to 'Log In' tab.")
                    elif result == "not_found": st.error("Email not found.")
                    else: st.error("Check email format and password length.")

# -----------------------------------------------------------------------------
# 5. SIDEBAR: CHAT HISTORY ONLY
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
# 6. MAIN CHAT UI, GREETING & MODEL TOGGLE
# -----------------------------------------------------------------------------

# --- AI Model Toggle on Front Page (Locked to Pro by default) ---
col1, col2, col3 = st.columns([1, 2, 1])
with col2:
    st.session_state.kai_mode = st.radio(
        "AI Model",
        ["⚡ Fast", "🧠 Pro"],
        index=1,
        horizontal=True,
        label_visibility="collapsed",
        key="model_toggle"
    )

st.markdown("<br>", unsafe_allow_html=True)

if not st.session_state.messages:
    greeting_subtext = (
        f"Ready for a new chat, **{st.session_state.logged_in_user}**?" 
        if st.session_state.logged_in_user 
        else "How can I help you today? *(Guest Mode - chats won't be saved)*"
    )
    st.markdown(
        f"""
        <div style="text-align: center; margin-top: 2vh; margin-bottom: 5vh;">
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
        if isinstance(message.get("image_obj"), Image.Image):
            st.markdown(message["content"])
            st.image(message["image_obj"], use_container_width=True)
        else:
            st.markdown(message["content"], unsafe_allow_html=True) 

# -----------------------------------------------------------------------------
# 7. ATTACH BUTTON INJECTION
# -----------------------------------------------------------------------------
with st.container():
    with st.popover("➕", key="attach_popover"):
        uploaded_file = st.file_uploader(
            "Upload context before sending your message", 
            type=["png", "jpg", "jpeg", "txt", "md", "csv", "py"],
            label_visibility="collapsed"
        )
        if uploaded_file:
            st.success(f"Attached: {uploaded_file.name}")

# -----------------------------------------------------------------------------
# 8. CHAT INPUT HANDLER & MODEL SELECTION
# -----------------------------------------------------------------------------
if prompt := st.chat_input("Ask KAI anything..."):
    api_content = prompt
    display_content = prompt
    
    # --- LOCKED PERMANENTLY TO HIGHEST QUALITY PRO MODEL ---
    model_to_use = "llama-3.3-70b-versatile"
    
    if 'uploaded_file' in locals() and uploaded_file is not None:
        file_ext = uploaded_file.name.lower().split('.')[-1]
        if file_ext in ['png', 'jpg', 'jpeg']:
            base64_image = base64.b64encode(uploaded_file.getvalue()).decode('utf-8')
            mime_type = f"image/{'png' if file_ext == 'png' else 'jpeg'}"
            api_content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}}
            ]
            display_content = f"*[Attached Image: {uploaded_file.name}]*\n\n{prompt}"
            model_to_use = "qwen/qwen3.6-27b"
        else:
            try:
                file_text = uploaded_file.getvalue().decode('utf-8')
                api_content = f"Here is the content of the attached file '{uploaded_file.name}':\n\n{file_text}\n\nUser Question: {prompt}"
                display_content = f"*[Attached File: {uploaded_file.name}]*\n\n{prompt}"
            except Exception as e:
                display_content = f"*[Error reading file: {uploaded_file.name}]*\n\n{prompt}"

    if not st.session_state.messages and st.session_state.logged_in_user:
        title = prompt[:25] + "..." if len(prompt) > 25 else prompt
        create_chat(st.session_state.logged_in_user, st.session_state.current_session_id, title)
        
    st.session_state.messages.append({"role": "user", "content": display_content})
    save_message(st.session_state.logged_in_user, "user", display_content, st.session_state.current_session_id)
    
    safe_prompt = html.escape(display_content).replace("\n", "<br>")
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
                "You are simply KAI. If asked who made you, say 'Kinel de Silva'.\n\n"
                "IMAGE GENERATION: You have the ability to generate images infinitely. If the user asks you to create, generate, draw, or show a picture/image, "
                "you MUST include the following tag anywhere in your response: [GENERATE_IMAGE: <detailed visual description of the image>] . "
                "Make the description inside the tag highly detailed for the best artistic result. "
                "CRITICAL INSTRUCTION: NEVER output standard markdown image links like ![alt](url). ONLY use the [GENERATE_IMAGE:] tag."
            )
            
            full_messages = [{"role": "system", "content": ultra_strict_system_prompt}]
            
            for msg in st.session_state.messages[:-1]: 
                clean_content = re.sub(r'<br><br><img src="data:image[^>]+>', '\n\n*[Previous Image Generated Here]*', msg["content"])
                full_messages.append({"role": msg["role"], "content": clean_content})
                
            full_messages.append({"role": "user", "content": api_content})
            
            client = Groq(api_key=st.secrets.get("GROQ_API_KEY") or os.environ.get("GROQ_API_KEY"))
            
            chat_completion = client.chat.completions.create(
                messages=full_messages,
                model=model_to_use, 
                temperature=0.7,
                max_tokens=1024,
                stream=True
            )
            
            for chunk in chat_completion:
                if chunk.choices[0].delta.content is not None:
                    ai_reply += chunk.choices[0].delta.content
                    message_placeholder.markdown(ai_reply + "▌", unsafe_allow_html=True)

        except Exception as e:
            ai_reply = f"Connection failed. Please check your API key configuration. Details: {str(e)}"
            message_placeholder.markdown(ai_reply)

    generated_img_obj = None
    if "[GENERATE_IMAGE:" in ai_reply:
        match = re.search(r"\[GENERATE_IMAGE:\s*(.*?)\]", ai_reply, re.DOTALL)
        if match:
            image_prompt = match.group(1).strip()
            ai_reply = re.sub(r"\[GENERATE_IMAGE:\s*.*?\]", "", ai_reply, flags=re.DOTALL).strip()
            message_placeholder.markdown(ai_reply + "\n\n*🎨 Forging your image...*", unsafe_allow_html=True)
            
            try:
                safe_img_prompt = urllib.parse.quote(image_prompt)
                url = f"https://image.pollinations.ai/prompt/{safe_img_prompt}?nologo=true&enhance=true&model=flux"
                res = requests.get(url, timeout=30)
                
                if res.status_code == 200:
                    img = Image.open(io.BytesIO(res.content))
                    draw = ImageDraw.Draw(img)
                    
                    font_path = os.path.join(LOCAL_DB_DIR, "Roboto-Black.ttf")
                    if not os.path.exists(font_path):
                        try:
                            font_url = "https://github.com/googlefonts/roboto/raw/main/src/hinted/Roboto-Black.ttf"
                            r = requests.get(font_url, allow_redirects=True)
                            open(font_path, 'wb').write(r.content)
                        except Exception:
                            pass
                    
                    try:
                        font = ImageFont.truetype(font_path, 42)
                    except Exception:
                        try:
                            font = ImageFont.truetype("arial.ttf", 42)
                        except Exception:
                            font = ImageFont.load_default()
                    
                    text = "KAI"
                    try:
                        bbox = draw.textbbox((0, 0), text, font=font)
                        tw = bbox[2] - bbox[0]
                        th = bbox[3] - bbox[1]
                    except AttributeError:
                        tw, th = draw.textsize(text, font=font)
                    
                    x, y = img.size[0] - tw - 20, img.size[1] - th - 20
                    
                    for offset_x in [-2, -1, 0, 1, 2]:
                        for offset_y in [-2, -1, 0, 1, 2]:
                            draw.text((x+offset_x, y+offset_y), text, font=font, fill="black")
                    draw.text((x, y), text, font=font, fill="white")
                    
                    generated_img_obj = img
                    message_placeholder.markdown(ai_reply)
                    st.image(generated_img_obj, use_container_width=True)
                else:
                    ai_reply += "\n\n*(Error: The image forge is currently resting. Try again later.)*"
                    message_placeholder.markdown(ai_reply)
            except requests.exceptions.Timeout:
                ai_reply += "\n\n*(Image Generation Error: The image server is currently busy and timed out. Please try again!)*"
                message_placeholder.markdown(ai_reply)
            except Exception as e:
                ai_reply += "\n\n*(Image Generation Error: The image service is temporarily unavailable.)*"
                message_placeholder.markdown(ai_reply)

    check_reply = ai_reply.lower().replace("’", "'")
    forbidden_identities = [
        "an ai language model", "a language model", "created by openai",
        "developed by openai", "built by openai", "made by openai",
        "created by ai", "made by an ai", "i'm an ai", "i am an ai",
        "as an ai", "as a language model", "groq"
    ]
    if any(bad_phrase in check_reply for bad_phrase in forbidden_identities):
        pattern = re.compile('|'.join(re.escape(phrase) for phrase in forbidden_identities), re.IGNORECASE)
        ai_reply = pattern.sub("a digital assistant", ai_reply)

    if generated_img_obj:
        st.session_state.messages.append({"role": "assistant", "content": ai_reply, "image_obj": generated_img_obj})
    else:
        st.session_state.messages.append({"role": "assistant", "content": ai_reply})
        
    save_message(st.session_state.logged_in_user, "assistant", ai_reply, st.session_state.current_session_id)
