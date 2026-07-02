"""
kugua init — scaffold a guarded agent template.

Generates a ready-to-run agent script with kugua SafetyManager
integrated as a Sidecar guardian.

Usage:
  kugua-init --framework=langgraph --output=./my_agent.py
  kugua-init --framework=langgraph                  # defaults to ./agent_with_guardian.py
  kugua-init --list                                 # list available templates
"""

import argparse
import sys
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

TEMPLATES = {
    "langgraph": {
        "file": "agent_with_guardian_langgraph.py.tmpl",
        "description": "LangGraph agent with SafetyManager gating on tool calls",
        "requires": "langgraph langchain-core",
        "output_default": "agent_with_guardian.py",
    },
}


def _list_templates():
    print("Available templates:\n")
    for name, info in TEMPLATES.items():
        print(f"  {name:20s}  {info['description']}")
        print(f"  {'':20s}  Dependencies: {info['requires']}")
        print()


def _scaffold(framework: str, output: str) -> int:
    info = TEMPLATES[framework]
    tmpl_path = TEMPLATES_DIR / info["file"]

    if not tmpl_path.exists():
        print(f"Error: template not found: {tmpl_path}", file=sys.stderr)
        return 1

    template_content = tmpl_path.read_text(encoding="utf-8")

    # Simple placeholder substitution
    output_path = Path(output)
    rendered = template_content.replace("${filename}", output_path.name)

    if output_path.exists():
        print(f"Warning: {output_path} already exists.", end=" ")
        resp = input("Overwrite? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("Aborted.")
            return 0

    output_path.write_text(rendered, encoding="utf-8")
    print(f"Created: {output_path}")
    print()
    print(f"Next steps:")
    print(f"  1. Install dependencies: pip install {info['requires']} kugua")
    print(f"  2. Run the agent:        python {output_path.name}")
    print(f"  3. Customize tools and LLM call in the generated file")
    print()
    print("The agent's tool calls are gated through safety.check_permission().")
    print("Adjust the trust level in the generated file to match your needs.")
    return 0


def main():
    p = argparse.ArgumentParser(
        description="kugua init — scaffold a guarded agent template",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  kugua-init --framework=langgraph
  kugua-init --framework=langgraph --output=./my_agent.py
  kugua-init --list
        """,
    )
    p.add_argument(
        "--framework", "-f",
        choices=list(TEMPLATES.keys()),
        default="langgraph",
        help="Agent framework to generate a template for",
    )
    p.add_argument(
        "--output", "-o",
        default=None,
        help="Output file path",
    )
    p.add_argument(
        "--list", "-l",
        action="store_true",
        help="List available templates and exit",
    )
    args = p.parse_args()

    if args.list:
        _list_templates()
        return 0

    info = TEMPLATES[args.framework]
    output = args.output or info["output_default"]

    return _scaffold(args.framework, output)


if __name__ == "__main__":
    sys.exit(main())
