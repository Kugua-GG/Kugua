"""Package kugua complete system for distribution."""
import shutil, os
from pathlib import Path

desktop = Path(r"C:\Users\Administrator\Desktop")
pkg_dir = desktop / "kugua-package"
if pkg_dir.exists():
    shutil.rmtree(pkg_dir)
pkg_dir.mkdir()

# ── 1. kugua-code kernel ──
src = desktop / "kugua"
dst = pkg_dir / "kugua-code"
shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
    "__pycache__", "*.pyc", ".git", "build", "dist", "*.egg-info"
))
print(f"[OK] kugua-code kernel -> {dst}")

# ── 2. kugua MCP server ──
mcp_src = desktop / "kugua-mcp"
mcp_dst = pkg_dir / "kugua-mcp"
if mcp_src.exists():
    shutil.copytree(mcp_src, mcp_dst, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc"
    ))
    print(f"[OK] kugua-mcp -> {mcp_dst}")
else:
    print(f"[WARN] kugua-mcp not found at {mcp_src}")

# ── 3. CLAUDE.md configs ──
claude_files = {
    "CLAUDE_global.md": Path(r"C:\Users\Administrator\.claude\CLAUDE.md"),
    "CLAUDE_desktop.md": desktop / "CLAUDE.md",
}
for name, src_path in claude_files.items():
    if src_path.exists():
        shutil.copy2(src_path, pkg_dir / name)
        print(f"[OK] {name} -> {pkg_dir / name}")
    else:
        print(f"[WARN] {name} not found")

# ── 4. MCP config ──
mcp_json = desktop / ".mcp.json"
if mcp_json.exists():
    shutil.copy2(mcp_json, pkg_dir / "mcp_config.json")
    print(f"[OK] mcp_config.json")

# ── 5. Tests ──
test_src = desktop / ".codex" / "scripts_archive"
test_dst = pkg_dir / "tests"
test_dst.mkdir(exist_ok=True)
for f in sorted(test_src.glob("*kugua*")):
    shutil.copy2(f, test_dst / f.name)
    print(f"[OK] test: {f.name}")

# ── 6. Install scripts ──
for f in (desktop / "kugua").glob("install.*"):
    shutil.copy2(f, pkg_dir / f.name)
    print(f"[OK] {f.name}")

# ── Stats ──
total_bytes = sum(
    f.stat().st_size for f in pkg_dir.rglob("*") if f.is_file()
)
print(f"\n{'='*50}")
print(f"Package: {pkg_dir}")
print(f"Total size: {total_bytes / 1024:.0f} KB")
print(f"Files: {sum(1 for f in pkg_dir.rglob('*') if f.is_file())}")
