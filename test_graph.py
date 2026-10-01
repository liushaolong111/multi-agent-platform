from dotenv import load_dotenv
load_dotenv()

from graph.service import graph

result = graph.invoke({
    "user_input": "分析上季度销售数据并生成一份报告",
    "messages": [],
    "status": "start",
    "iteration": 0,
    "session_id": "test_session_001",
    "user_id": "test_user_001"
})

print("\n" + "=" * 50)
print("【最终结果】")
print("=" * 50)
print("任务类型:", result.get("task_type"))
print("规划列表:", result.get("plan"))
print("审核结论:", result.get("review_verdict"))
print("最终回答:", result.get("final_answer", "")[:300])