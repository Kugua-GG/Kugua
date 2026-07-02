"""kugua worker — CLI entry point for TaskExecutor.execute()"""
import argparse, sys, json
from pathlib import Path
from kugua import KuguaConfig, LLMClient, TaskExecutor


def main():
    p = argparse.ArgumentParser(description="kugua worker")
    p.add_argument("--subtask-id", required=True, help="Subtask identifier")
    p.add_argument("--task", required=True, help="Task description")
    p.add_argument("--context", default="", help="Additional context")
    p.add_argument("--model", default=None, help="Model override")
    args = p.parse_args()

    cfg = KuguaConfig.from_env()
    client = LLMClient(cfg)
    executor = TaskExecutor(client, cfg)

    result = executor.execute(
        subtask_id=args.subtask_id,
        task=args.task,
        context=args.context,
        model=args.model,
    )

    output = result.__dict__
    output["usage"] = result.usage
    output["elapsed_ms"] = result.elapsed_ms
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
