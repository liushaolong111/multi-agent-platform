"""离线评测脚本：用带标准答案的评测集量化系统的端到端回答正确率。

评分方式（两级，可叠加）：
1. 关键词匹配（默认）：标准答案中的关键数字/结论必须出现在回答中，规则确定、零成本
2. LLM-as-judge（--judge 开启）：让 LLM 语义判断回答是否正确，覆盖关键词匹配不到的表述

运行方式（需要 DEEPSEEK_API_KEY 和已构建的 FAISS 索引，不进 CI）：
    .venv/Scripts/python.exe evals/run_eval.py                # 关键词评分
    .venv/Scripts/python.exe evals/run_eval.py --judge        # 关键词 + LLM 评委
    .venv/Scripts/python.exe evals/run_eval.py --limit 5      # 只跑前 5 题（快速冒烟）

输出：控制台报告 + evals/report.json（可用于对比历次改动前后的效果回归）
"""
import argparse
import json
import logging
import os
import sys
import time

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from graph.llm import safe_invoke  # noqa: E402
from graph.service import graph  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("eval")
logger.setLevel(logging.INFO)

DATASET_PATH = os.path.join(os.path.dirname(__file__), "dataset.jsonl")
REPORT_PATH = os.path.join(os.path.dirname(__file__), "report.json")


def load_dataset(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def keyword_score(item: dict, answer: str) -> bool:
    """规则评分：expected_all 全部命中，且 expected_any（如有）至少命中一个。"""
    expected_all = item.get("expected_all") or []
    expected_any = item.get("expected_any") or []
    if expected_all and not all(k in answer for k in expected_all):
        return False
    if expected_any and not any(k in answer for k in expected_any):
        return False
    return bool(expected_all or expected_any)


def llm_judge_score(item: dict, answer: str) -> bool:
    """LLM-as-judge：语义层面判断回答是否覆盖了标准答案要点。"""
    reference = "；".join(
        (item.get("expected_all") or []) + (item.get("expected_any") or [])
    )
    prompt = f"""你是严格的评测评委。判断【候选回答】是否正确回答了【问题】，
且关键结论与【参考要点】一致（允许不同的数字格式和表述方式）。

问题：{item['question']}
参考要点：{reference}
候选回答：{answer[:1500]}

只回答 CORRECT 或 WRONG，一个词。"""
    raw = safe_invoke(prompt, fallback="WRONG")
    return "CORRECT" in raw.upper() and "WRONG" not in raw.upper()


def run_eval(dataset: list, use_judge: bool) -> dict:
    results = []
    for i, item in enumerate(dataset, 1):
        start = time.perf_counter()
        try:
            state = graph.invoke(
                {
                    "user_input": item["question"],
                    "messages": [],
                    "status": "start",
                    "iteration": 0,
                    "session_id": f"eval_{item['id']}",
                    "user_id": "eval_user",
                }
            )
            answer = state.get("final_answer") or state.get("tool_result", "")
        except Exception as e:
            answer = f"（执行异常：{e}）"
        elapsed = time.perf_counter() - start

        kw_pass = keyword_score(item, answer)
        judge_pass = llm_judge_score(item, answer) if use_judge else None
        passed = (kw_pass and judge_pass) if use_judge else kw_pass

        results.append(
            {
                "id": item["id"],
                "category": item["category"],
                "question": item["question"],
                "keyword_pass": kw_pass,
                "judge_pass": judge_pass,
                "pass": passed,
                "latency_s": round(elapsed, 1),
                "answer_preview": answer[:300],
            }
        )
        mark = "✓" if passed else "✗"
        print(f"  [{mark}] #{item['id']:>2} ({item['category']}) {item['question']}  ({elapsed:.1f}s)")

    # 按类别汇总
    categories = {}
    for r in results:
        cat = categories.setdefault(r["category"], {"total": 0, "pass": 0})
        cat["total"] += 1
        cat["pass"] += int(r["pass"])

    total = len(results)
    passed = sum(1 for r in results if r["pass"])
    return {
        "overall": {"total": total, "pass": passed, "accuracy": round(passed / total, 3)},
        "by_category": {
            k: {**v, "accuracy": round(v["pass"] / v["total"], 3)}
            for k, v in sorted(categories.items())
        },
        "judge_enabled": use_judge,
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description="多智能体平台离线评测")
    parser.add_argument("--judge", action="store_true", help="开启 LLM-as-judge 语义评分")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题")
    parser.add_argument("--dataset", default=DATASET_PATH, help="评测集路径")
    parser.add_argument("--output", default=REPORT_PATH, help="报告输出路径")
    args = parser.parse_args()

    if config.check_llm_config():
        print("错误：未配置 DEEPSEEK_API_KEY，评测需要真实 LLM。")
        sys.exit(1)

    dataset = load_dataset(args.dataset)
    if args.limit:
        dataset = dataset[: args.limit]

    print(f"开始评测：{len(dataset)} 题（LLM-as-judge：{'开' if args.judge else '关'}）\n")
    start = time.perf_counter()
    report = run_eval(dataset, args.judge)
    report["elapsed_s"] = round(time.perf_counter() - start, 1)

    print(f"\n总体正确率：{report['overall']['pass']}/{report['overall']['total']}"
          f" = {report['overall']['accuracy'] * 100:.1f}%")
    for cat, stat in report["by_category"].items():
        print(f"  - {cat}: {stat['pass']}/{stat['total']} = {stat['accuracy'] * 100:.1f}%")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告已保存：{args.output}（耗时 {report['elapsed_s']}s）")


if __name__ == "__main__":
    main()
