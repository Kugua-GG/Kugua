"""kugua checker — CLI entry point for TaskExecutor.review()"""
import argparse, sys, json
from kugua import KuguaConfig, LLMClient, TaskExecutor


def main():
    p = argparse.ArgumentParser(description="kugua checker")
    p.add_argument("--subtask-id", required=True, help="Subtask identifier")
    p.add_argument("--worker-output", required=True, help="Worker output to review")
    p.add_argument("--requirements", default="准确性、完整性、合规性", help="Review criteria")
    p.add_argument("--model", default=None, help="Model override")
    args = p.parse_args()

    cfg = KuguaConfig.from_env()
    client = LLMClient(cfg)
    executor = TaskExecutor(client, cfg)

    result = executor.review(
        subtask_id=args.subtask_id,
        worker_output=args.worker_output,
        requirements=args.requirements,
        model=args.model,
    )

    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2, default=str))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
