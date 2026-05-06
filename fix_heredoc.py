import re, pathlib

FILES = [
    r"g:\adaptive-trading-bot\.github\workflows\build-mt5-base.yml",
    r"g:\adaptive-trading-bot\.github\workflows\build-mt5-base-dispatch.yml",
]

# Pattern covers both indent variants (14 or 12 leading spaces on ASSET_ID line)
HEREDOC_RE = re.compile(
    r'( +)ASSET_ID="\$\(python - <<PY\n'
    r'import json, sys\n'
    r'data=json\.loads\(sys\.stdin\.read\(\)\)\n'
    r'assets=data\.get\("assets", \[\]\)\n'
    r'target="\$\{ASSET\}"\n'
    r'for a in assets:\n'
    r'    if a\.get\("name"\) == target:\n'
    r'        print\(a\.get\("id"\)\)\n'
    r'        raise SystemExit\(0\)\n'
    r'print\(""\)\n'
    r'PY\n'
    r' +<<<  "\$\{API_JSON\}"\)"',
    re.MULTILINE,
)

# Simpler fallback: find the block by its start+end markers
HEREDOC_BLOCK = re.compile(
    r'( +ASSET_ID="\$\(python - <<PY\n(?:.*\n)*?PY\n +<<< "\$\{API_JSON\}"\)")',
    re.MULTILINE,
)

JQ_LINE = (
    '{indent}ASSET_ID="$(echo "${{API_JSON}}" | '
    "jq -r --arg name \"${{ASSET}}\" '.assets[] | select(.name == $name) | .id')\""
)

for fp in FILES:
    p = pathlib.Path(fp)
    if not p.exists():
        print(f"SKIP (not found): {fp}")
        continue
    text = p.read_text(encoding="utf-8")
    m = HEREDOC_BLOCK.search(text)
    if m:
        full_block = m.group(1)
        indent = len(full_block) - len(full_block.lstrip())
        replacement = JQ_LINE.format(indent=" " * indent)
        new_text = text.replace(full_block, replacement)
        p.write_text(new_text, encoding="utf-8")
        print(f"FIXED: {p.name}")
    else:
        # show surrounding context to help debug
        idx = text.find("import json, sys")
        if idx >= 0:
            print(f"HEREDOC FOUND but pattern didn't match in {p.name}:")
            print(repr(text[max(0,idx-120):idx+200]))
        else:
            print(f"No heredoc found in {p.name} (already fixed?)")
