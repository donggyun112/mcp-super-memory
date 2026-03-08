import time
import webbrowser

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from . import credentials
from .claude_oauth import OAuthManager
from .chat import ChatSession
from .memory_graph import MemoryGraph

CHAT_HTML = """\
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Super Memory</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #0a0a0f; --surface: #12121a; --surface2: #1a1a26;
    --border: rgba(255,255,255,0.08); --text: #e8e8f0; --text-muted: #6b6b80;
    --accent: #6366f1; --accent-hover: #818cf8;
    --user-bg: #1e1b4b; --user-border: rgba(99,102,241,0.3);
    --bot-bg: #12121a; --bot-border: rgba(255,255,255,0.08);
  }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; }
  .header { padding: 16px 20px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 12px; background: var(--surface); }
  .header h1 { font-size: 16px; font-weight: 600; flex: 1; }
  .header .mem-count { font-size: 12px; color: var(--text-muted); background: var(--surface2); padding: 4px 10px; border-radius: 12px; border: 1px solid var(--border); cursor: pointer; }
  .header a { font-size: 12px; color: var(--text-muted); text-decoration: none; }
  .header a:hover { color: var(--text); }
  .messages { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 12px; }
  .msg { max-width: 75%; padding: 12px 16px; border-radius: 16px; font-size: 14px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
  .msg.user { align-self: flex-end; background: var(--user-bg); border: 1px solid var(--user-border); border-bottom-right-radius: 4px; }
  .msg.bot { align-self: flex-start; background: var(--bot-bg); border: 1px solid var(--bot-border); border-bottom-left-radius: 4px; }
  .typing { align-self: flex-start; color: var(--text-muted); font-size: 13px; padding: 8px 0; }
  .typing span { animation: blink 1.4s infinite; }
  .typing span:nth-child(2) { animation-delay: 0.2s; }
  .typing span:nth-child(3) { animation-delay: 0.4s; }
  @keyframes blink { 0%,60%,100% { opacity: 0.2; } 30% { opacity: 1; } }
  .input-area { padding: 16px 20px; border-top: 1px solid var(--border); background: var(--surface); display: flex; gap: 10px; }
  .input-area textarea { flex: 1; background: var(--surface2); border: 1px solid var(--border); border-radius: 12px; padding: 10px 14px; color: var(--text); font-size: 14px; font-family: inherit; resize: none; outline: none; min-height: 44px; max-height: 120px; }
  .input-area textarea::placeholder { color: var(--text-muted); }
  .input-area textarea:focus { border-color: rgba(99,102,241,0.4); }
  .input-area button { background: var(--accent); border: none; border-radius: 12px; padding: 0 20px; color: #fff; font-size: 14px; font-weight: 500; cursor: pointer; transition: background 0.15s; }
  .input-area button:hover { background: var(--accent-hover); }
  .input-area button:disabled { opacity: 0.4; cursor: default; }
  .mem-panel { display: none; position: fixed; top: 0; right: 0; width: 380px; height: 100vh; background: var(--surface); border-left: 1px solid var(--border); overflow-y: auto; padding: 20px; z-index: 10; }
  .mem-panel.open { display: block; }
  .mem-panel h2 { font-size: 14px; font-weight: 600; margin-bottom: 16px; display: flex; justify-content: space-between; }
  .mem-panel h2 span { cursor: pointer; color: var(--text-muted); }
  .mem-item { padding: 10px 12px; border: 1px solid var(--border); border-radius: 10px; margin-bottom: 8px; font-size: 13px; }
  .mem-item .content { margin-bottom: 6px; }
  .mem-item .tags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
  .mem-item .tag { font-size: 11px; border-radius: 4px; padding: 1px 6px; }
  .mem-item .tag.key { background: rgba(99,102,241,0.1); color: var(--accent); }
  .mem-item .tag.depth { background: rgba(34,197,94,0.1); color: #22c55e; }
  .mem-item .tag.access { background: rgba(251,191,36,0.1); color: #fbbf24; }
  .mem-item .tag.updated { background: rgba(239,68,68,0.1); color: #ef4444; }
</style>
</head>
<body>
  <div class="header">
    <h1>Super Memory</h1>
    <div class="mem-count" id="mem-count" onclick="toggleMem()">0 memories</div>
    <a href="/logout">logout</a>
  </div>
  <div class="messages" id="messages"></div>
  <div class="input-area">
    <textarea id="input" placeholder="메시지를 입력하세요..." rows="1" onkeydown="handleKey(event)"></textarea>
    <button id="send-btn" onclick="sendMsg()">전송</button>
  </div>
  <div class="mem-panel" id="mem-panel">
    <h2>Memories <span onclick="toggleMem()">&times;</span></h2>
    <div id="mem-list"></div>
  </div>
<script>
let sid = null;
let sending = false;
const msgs = document.getElementById('messages');
const input = document.getElementById('input');
const btn = document.getElementById('send-btn');

input.addEventListener('input', () => {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 120) + 'px';
});
function handleKey(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMsg(); } }

function addMsg(role, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  d.textContent = text;
  msgs.appendChild(d);
  msgs.scrollTop = msgs.scrollHeight;
}
function showTyping() {
  const d = document.createElement('div');
  d.className = 'typing'; d.id = 'typing';
  for (let i = 0; i < 3; i++) { const s = document.createElement('span'); s.textContent = '.'; d.appendChild(s); }
  msgs.appendChild(d);
  msgs.scrollTop = msgs.scrollHeight;
}
function hideTyping() { const el = document.getElementById('typing'); if (el) el.remove(); }

async function sendMsg() {
  const text = input.value.trim();
  if (!text || sending) return;
  sending = true; btn.disabled = true;
  input.value = ''; input.style.height = 'auto';
  addMsg('user', text);
  showTyping();
  try {
    const body = { message: text };
    if (sid) body.session_id = sid;
    const r = await fetch('/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await r.json();
    hideTyping();
    if (data.error) { addMsg('bot', 'Error: ' + data.error); }
    else {
      sid = data.session_id;
      if (data.tools_used && data.tools_used.length > 0) {
        const toolDiv = document.createElement('div');
        toolDiv.className = 'msg bot';
        toolDiv.style.fontSize = '12px';
        toolDiv.style.color = 'var(--text-muted)';
        toolDiv.style.borderColor = 'rgba(99,102,241,0.2)';
        toolDiv.style.background = 'rgba(99,102,241,0.05)';
        const lines = data.tools_used.map(t => {
          const args = typeof t.input === 'object' ? JSON.stringify(t.input) : t.input;
          return t.tool + '(' + args + ')';
        });
        toolDiv.textContent = 'tools: ' + lines.join(', ');
        msgs.appendChild(toolDiv);
      }
      addMsg('bot', data.reply);
    }
    loadMemCount();
  } catch (e) { hideTyping(); addMsg('bot', 'Error: ' + e.message); }
  sending = false; btn.disabled = false; input.focus();
}

async function loadMemCount() {
  try {
    const r = await fetch('/memory/list');
    const items = await r.json();
    document.getElementById('mem-count').textContent = items.length + ' memories';
  } catch (_) {}
}

async function toggleMem() {
  const panel = document.getElementById('mem-panel');
  if (panel.classList.contains('open')) { panel.classList.remove('open'); return; }
  const r = await fetch('/memory/list');
  const items = await r.json();
  const list = document.getElementById('mem-list');
  list.textContent = '';
  for (const m of items) {
    const d = document.createElement('div');
    d.className = 'mem-item';
    const content = document.createElement('div');
    content.className = 'content';
    content.textContent = m.content;
    d.appendChild(content);
    const meta = document.createElement('div');
    meta.className = 'tags';
    // keys
    if (m.keys) {
      for (const k of m.keys) {
        const tag = document.createElement('span');
        tag.className = 'tag key';
        tag.textContent = k;
        meta.appendChild(tag);
      }
    }
    // depth
    const depthTag = document.createElement('span');
    depthTag.className = 'tag depth';
    const depthLabel = m.depth < 0.3 ? 'shallow' : m.depth < 0.7 ? 'medium' : 'deep';
    depthTag.textContent = depthLabel + ' ' + m.depth;
    meta.appendChild(depthTag);
    // access count
    if (m.access_count > 0) {
      const accTag = document.createElement('span');
      accTag.className = 'tag access';
      accTag.textContent = m.access_count + 'x recalled';
      meta.appendChild(accTag);
    }
    if (m.supersedes) {
      const sup = document.createElement('span');
      sup.className = 'tag updated';
      sup.textContent = 'updated';
      meta.appendChild(sup);
    }
    d.appendChild(meta);
    list.appendChild(d);
  }
  panel.classList.add('open');
}

loadMemCount();
input.focus();
</script>
</body>
</html>
"""

app = FastAPI()
oauth = OAuthManager()
graph = MemoryGraph()
_chat_sessions: dict[str, ChatSession] = {}

# In-memory session (loaded from disk on startup)
_session: credentials.Session | None = None


@app.on_event("startup")
async def _startup():
    global _session
    _session = credentials.load()
    graph.load()
    if _session:
        print(f"[super-memory] Loaded saved session (token: ...{_session.access_token[-8:]})")
    else:
        print("[super-memory] No saved session found")
    print(f"[super-memory] {len(graph.keys)} keys, {len(graph.memories)} memories, {len(graph.links)} links")


@app.get("/")
async def index():
    if not _session:
        return RedirectResponse("/login")
    return HTMLResponse(CHAT_HTML)


@app.get("/login")
async def login():
    login_id, auth_url = oauth.start()
    webbrowser.open(auth_url)
    return HTMLResponse(f"""
    <h2>Anthropic 로그인 페이지가 열렸습니다</h2>
    <p>로그인 후 표시된 코드를 아래에 붙여넣으세요.</p>
    <form action="/callback" method="get">
        <input type="hidden" name="login_id" value="{login_id}" />
        <input type="text" name="code" placeholder="코드를 여기에 붙여넣으세요" style="width:300px;padding:8px;" />
        <button type="submit" style="padding:8px 16px;">확인</button>
    </form>
    """)


@app.get("/callback")
async def callback(login_id: str, code: str):
    global _session
    code = code.split("#")[0].strip()
    token = await oauth.complete(login_id, code)

    expires_at = None
    if token.expires_in:
        expires_at = int(time.time() * 1000) + token.expires_in * 1000 - 5 * 60 * 1000

    session = credentials.Session(
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        expires_at=expires_at,
    )
    _session = session
    credentials.save(session)

    return HTMLResponse("<h2>로그인 성공!</h2><p>이 창을 닫아도 됩니다.</p><a href='/'>홈으로</a>")


@app.get("/logout")
async def logout():
    global _session
    _session = None
    credentials.clear()
    return HTMLResponse("<h2>로그아웃 완료</h2><a href='/'>홈으로</a>")


@app.get("/session")
async def get_session():
    if not _session:
        return {"logged_in": False}
    return {
        "logged_in": True,
        "expires_at": _session.expires_at,
    }


# ── Chat API ──


class ChatMessage(BaseModel):
    message: str
    session_id: str | None = None


@app.post("/chat")
async def chat(body: ChatMessage):
    if not _session:
        return {"error": "not logged in"}

    sid = body.session_id
    if not sid or sid not in _chat_sessions:
        cs = ChatSession(_session.access_token, graph)
        sid = cs.session_id
        _chat_sessions[sid] = cs
    else:
        cs = _chat_sessions[sid]

    result = await cs.send(body.message)
    return {"session_id": sid, "reply": result["reply"], "tools_used": result["tools_used"]}


@app.get("/chat/sessions")
async def list_chat_sessions():
    return [
        {"session_id": sid, "turns": len(cs.messages) // 2}
        for sid, cs in _chat_sessions.items()
    ]


# ── Memory API ──


@app.get("/memory/search")
async def search_memory(q: str):
    return await graph.recall(q)


@app.get("/memory/list")
async def list_memories():
    return graph.list_all()


@app.get("/memory/{memory_id}")
async def get_memory(memory_id: str):
    if memory_id not in graph.memories:
        return {"error": "not found"}
    mem = graph.memories[memory_id]
    keys = graph.get_keys_for_memory(memory_id)
    related = graph.get_related(memory_id)
    return {
        "id": memory_id,
        "content": mem.content,
        "keys": keys,
        "depth": round(mem.depth, 3),
        "access_count": mem.access_count,
        "supersedes": mem.supersedes,
        "source": mem.source,
        "created_at": mem.created_at,
        "related": related,
    }
