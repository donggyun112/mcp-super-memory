import json
import uuid

import httpx

from .memory_graph import MemoryGraph, save_turn, load_conversation

CLAUDE_API = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"

MEMORY_SYSTEM = """\
You are a helpful assistant. You have memory — use it silently.

## CRITICAL: Behavior
- **NEVER mention the memory system to the user.** No "기억했어요", "저장했습니다", "메모리에서 찾았어요".
- Act like you naturally know things. If you recall the user's name, just use it. Don't say "기억에 의하면".
- Save and recall silently in the background. The user should feel like talking to someone who just knows them.
- ❌ "동건님이시군요! 기억해뒀어요!" → ✅ "안녕 동건! 뭐 도와줄까?"
- ❌ "메모리를 검색해볼게요" → ✅ (just recall silently and answer)

## Memory System (internal, never expose)
N:M associative memory. Key Space (concepts) ↔ Value Space (memories).
Depth: 0.0 shallow ~ 1.0 deep. Deeper = more stable.

Stats: {stats}

## Rules

### Recall
1. ALWAYS recall first on new conversations. Silently.
2. Never say "I don't know" without recalling first.
3. Use SPECIFIC queries, not vague ones. Multiple targeted recalls beat one broad recall.
   - ❌ recall("사용자 정보") — too vague
   - ✅ recall("이름"), recall("직업"), recall("취향") — specific, multiple
4. If `superseded_by` exists, prefer the newer version.

### Remember
4. Save important info with good key concepts. Silently — don't announce it.
5. Keys = what searches should find this. Topics, categories, attributes.
6. **Names only as keys for identity memories.**
   - "사용자 이름은 동건" → keys: ["이름", "사용자", "동건"]
   - "좋아하는 과일은 딸기" → keys: ["과일", "딸기", "좋아함", "취향"] ← no name
7. Set `key_types` for names/proper nouns:
   - `"name"`: exact match only. `"proper_noun"`: exact match only.
   Example: key_types: {{"동건": "name"}}

### Correct
8. Use `correct` when info changes. Don't use `remember` for corrections.

### Explore
9. `recall` does 2-hop associative search automatically.
10. Use `related` for deeper exploration.

### Delete
11. `forget` only for truly wrong information.
"""

TOOLS = [
    {
        "name": "recall",
        "description": "N:M multi-hop search. Hop 1: find matching keys → linked memories. Hop 2: those memories' OTHER keys → more memories (score decayed). Results include 'hop' field (1=direct, 2=associative). Recalled memories become deeper.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for. Natural language — finds matching concepts in Key Space.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max results (default 5)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "remember",
        "description": "Save a new memory with key concepts (N:M). Keys are access points — the more diverse keys you provide, the more ways this memory can be discovered through associative search.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Clear, self-contained summary of what to remember",
                },
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Key concepts for this memory. Include: names, topics, categories, related concepts.",
                },
                "key_types": {
                    "type": "object",
                    "description": "Type for specific keys. Map of concept -> type. Types: 'name' (person names, exact match only), 'proper_noun' (brands, places, exact match only), 'concept' (default, semantic match). Example: {\"Donggeon\": \"name\", \"Python\": \"proper_noun\"}",
                },
            },
            "required": ["content", "keys"],
        },
    },
    {
        "name": "correct",
        "description": "Update a memory by creating a new version. Deep memories (depth > 0.7) resist change. Old version preserved but weakened.",
        "input_schema": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "ID of the memory to supersede",
                },
                "content": {
                    "type": "string",
                    "description": "The corrected/updated information",
                },
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Updated key concepts. Omit to inherit keys from old memory.",
                },
                "key_types": {
                    "type": "object",
                    "description": "Type for specific keys: 'name', 'proper_noun', or 'concept' (default).",
                },
            },
            "required": ["memory_id", "content"],
        },
    },
    {
        "name": "related",
        "description": "Find memories that share keys with a given memory. This is associative thinking — discover connections through shared concepts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "ID of the memory to explore from",
                },
            },
            "required": ["memory_id"],
        },
    },
    {
        "name": "get_conversation",
        "description": "Load original conversation turns. Use when a memory summary isn't detailed enough.",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID from memory's source field",
                },
                "turn": {
                    "type": "integer",
                    "description": "Turn number to center on. Omit for full conversation.",
                },
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "forget",
        "description": "Permanently delete a memory. Only for truly wrong information.",
        "input_schema": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "ID of the memory to delete",
                },
            },
            "required": ["memory_id"],
        },
    },
]


class ChatSession:
    def __init__(self, access_token: str, graph: MemoryGraph):
        self.access_token = access_token
        self.graph = graph
        self.session_id = uuid.uuid4().hex[:12]
        self.messages: list[dict] = []
        self._last_user_turn: int = 0

    def _build_system(self) -> str:
        n_keys = len(self.graph.keys)
        n_mems = len(self.graph.memories)
        n_links = len(self.graph.links)
        stats = f"{n_keys} keys, {n_mems} memories, {n_links} links"
        return MEMORY_SYSTEM.format(stats=stats)

    async def _handle_tool(self, name: str, input_data: dict) -> str:
        if name == "recall":
            results = await self.graph.recall(input_data["query"], input_data.get("top_k", 5))
            return json.dumps(results, ensure_ascii=False)

        if name == "remember":
            source = {"session_id": self.session_id, "turn": self._last_user_turn}
            keys = input_data["keys"]
            if isinstance(keys, str):
                try:
                    keys = json.loads(keys)
                except (json.JSONDecodeError, TypeError):
                    keys = [keys]
            nid = await self.graph.add(
                input_data["content"], keys,
                key_types=input_data.get("key_types"), source=source,
            )
            print(f"[memory] remember {nid} keys={input_data['keys']}: {input_data['content'][:80]}")
            return json.dumps({"saved": nid})

        if name == "correct":
            source = {"session_id": self.session_id, "turn": self._last_user_turn}
            nid = await self.graph.supersede(
                input_data["memory_id"], input_data["content"],
                key_concepts=input_data.get("keys"),
                key_types=input_data.get("key_types"), source=source,
            )
            print(f"[memory] correct {input_data['memory_id']} -> {nid}")
            return json.dumps({"new_id": nid, "superseded": input_data["memory_id"]})

        if name == "related":
            results = self.graph.get_related(input_data["memory_id"])
            return json.dumps(results, ensure_ascii=False)

        if name == "get_conversation":
            turns = load_conversation(input_data["session_id"], input_data.get("turn"))
            return json.dumps(turns, ensure_ascii=False)

        if name == "forget":
            ok = await self.graph.delete(input_data["memory_id"])
            print(f"[memory] forget {input_data['memory_id']} -> {ok}")
            return json.dumps({"deleted": ok})

        return json.dumps({"error": f"unknown tool: {name}"})

    async def _call_api(self) -> dict:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                CLAUDE_API,
                headers=_build_headers(self.access_token),
                json={
                    "model": MODEL,
                    "max_tokens": 4096,
                    "system": self._build_system(),
                    "messages": self.messages,
                    "tools": TOOLS,
                },
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Claude API error {resp.status_code}: {resp.text}")
        return resp.json()

    async def send(self, user_message: str) -> dict:
        self.messages.append({"role": "user", "content": user_message})
        self._last_user_turn = save_turn(self.session_id, "user", user_message)
        tool_log: list[dict] = []

        while True:
            data = await self._call_api()
            stop_reason = data.get("stop_reason")
            content_blocks = data.get("content", [])

            self.messages.append({"role": "assistant", "content": content_blocks})

            if stop_reason == "tool_use":
                tool_results = []
                for block in content_blocks:
                    if block.get("type") == "tool_use":
                        result = await self._handle_tool(block["name"], block["input"])
                        print(f"[tool] {block['name']}({json.dumps(block['input'], ensure_ascii=False)}) -> {result[:200]}")
                        tool_log.append({"tool": block["name"], "input": block["input"]})
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block["id"],
                            "content": result,
                        })
                self.messages.append({"role": "user", "content": tool_results})
                continue

            reply_parts = [b["text"] for b in content_blocks if b.get("type") == "text"]
            reply = "\n".join(reply_parts)
            save_turn(self.session_id, "assistant", reply)
            return {"reply": reply, "tools_used": tool_log}


def _build_headers(token: str) -> dict[str, str]:
    headers = {
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    if "sk-ant-oat" in token:
        headers.update({
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
            "user-agent": "claude-cli/1.0 (external, cli)",
            "x-app": "cli",
        })
    else:
        headers["x-api-key"] = token
    return headers
