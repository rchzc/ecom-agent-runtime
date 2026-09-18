"""多轮会话记忆：按 session_id 维护历史，支持上下文拼装（替代 Redis 会话态）。

面试话术锚点：售前咨询 Agent 的「多轮指代解析」依赖它——用户说
「它有什么优惠」时，能从历史定位到上一轮提到的商品。
"""


class ConversationMemory:
    def __init__(self, max_turns: int = 10):
        self.store = {}  # session_id -> list[{role, content}]
        self.max_turns = max_turns

    def append(self, session_id: str, role: str, content: str):
        self.store.setdefault(session_id, []).append({"role": role, "content": content})
        if len(self.store[session_id]) > self.max_turns * 2:
            self.store[session_id] = self.store[session_id][-self.max_turns * 2:]

    def history(self, session_id: str) -> list:
        return self.store.get(session_id, [])

    def last_user_query(self, session_id: str) -> str:
        for m in reversed(self.store.get(session_id, [])):
            if m["role"] == "user":
                return m["content"]
        return ""

    def clear(self, session_id: str):
        self.store.pop(session_id, None)


if __name__ == "__main__":
    m = ConversationMemory()
    m.append("s1", "user", "推荐一款降噪耳机")
    m.append("s1", "assistant", "推荐 A 款主动降噪耳机")
    m.append("s1", "user", "它有什么优惠")
    print("历史:", m.history("s1"))
    print("上轮用户:", m.last_user_query("s1"))
