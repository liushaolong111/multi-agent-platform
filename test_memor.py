from dotenv import load_dotenv
load_dotenv()

from memory.short_term import store_session, get_session, update_session
from memory.long_term import store_long_term, get_long_term

# 测试 Redis 短期记忆
print("=== 测试 Redis 短期记忆 ===")
store_session("test_001", {"user_input": "分析数据", "status": "started"}, ttl=60)
session = get_session("test_001")
print(f"读回会话：{session}")

update_session("test_001", "last_interaction", "分析完成")
session = get_session("test_001")
print(f"更新后：{session}")

# 测试 PostgreSQL 长期记忆
print("\n=== 测试 PostgreSQL 长期记忆 ===")
store_long_term("user_001", "test_001", {"action": "query", "result": "success"})
records = get_long_term("user_001", limit=5)
print(f"读回 {len(records)} 条记录")
for r in records:
    print(f"  - user_id={r.user_id}, session_id={r.session_id}, content={r.content}")