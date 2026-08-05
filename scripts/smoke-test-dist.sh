#!/usr/bin/env bash
# Build the sdist + wheel and smoke-test *the published artifacts* (review §2.1):
#   - the wheel ships the full cairn package (not just py.typed);
#   - each artifact installs into a clean venv, `import cairn` works, and both
#     console entry points (`cairn`, `cairn-cli`) respond to --help;
#   - the sdist reproduces the wheel's module set (install-from-sdist yields
#     the same installed package tree).
#
# fsdantic is a git-only dependency (not on PyPI), so the clean venvs install
# the exact pinned ref first, then the artifact.
#
# Usage: scripts/smoke-test-dist.sh   (needs uv + python3 on PATH; the devenv
# shell provides both).  CI: .github/workflows/ci.yml -> dist-smoke job.
set -euo pipefail
cd "$(dirname "$0")/.."

DIST_DIR="$(mktemp -d)"
VENVS_DIR="$(mktemp -d)"
trap 'rm -rf "$DIST_DIR" "$VENVS_DIR"' EXIT

FSDANTIC_URL="https://github.com/Bullish-Design/fsdantic.git"
FSDANTIC_REF="v0.7.0"
# fsdantic depends on agentfs-sdk via a URL dependency (git), which uv's pip
# resolver refuses to carry transitively — install it as a direct requirement
# first, mirroring the project's own uv.lock pin (v0.6.4-pyturso-0.7.2).
AGENTFS_URL="git+https://github.com/Bullish-Design/agentfs@v0.6.4-pyturso-0.7.2#subdirectory=sdk/python"

echo "==> Building sdist + wheel"
uv build --out-dir "$DIST_DIR" >/dev/null

shopt -s nullglob
sdist_paths=("$DIST_DIR"/cairn-*.tar.gz)
wheel_paths=("$DIST_DIR"/cairn-*.whl)
[[ ${#sdist_paths[@]} -eq 1 && ${#wheel_paths[@]} -eq 1 ]] || {
    echo "expected exactly one sdist and one wheel in $DIST_DIR" >&2
    exit 1
}
SDIST="${sdist_paths[0]}"
WHEEL="${wheel_paths[0]}"
echo "    sdist: $(basename "$SDIST")"
echo "    wheel: $(basename "$WHEEL")"

echo "==> Wheel contents: full package present"
python3 - "$WHEEL" <<'PY'
import sys
import zipfile

wheel = sys.argv[1]
names = set(zipfile.ZipFile(wheel).namelist())
required = {
    "cairn/__init__.py",
    "cairn/py.typed",
    "cairn/cli/cli.py",
    "cairn/cli/typer_cli.py",
    "cairn/core/exceptions.py",
    "cairn/orchestrator/orchestrator.py",
    "cairn/orchestrator/lifecycle.py",
    "cairn/orchestrator/signals.py",
    "cairn/providers/providers.py",
    "cairn/runtime/sandbox/sandbox.py",
    "cairn/runtime/sandbox/boot.py",
    "cairn/runtime/workspace_manager.py",
    "cairn/watcher/watcher.py",
}
missing = sorted(required - names)
if missing:
    sys.exit(f"wheel is missing modules: {missing}")
print(f"    wheel OK: {sum(1 for n in names if n.endswith('.py'))} python modules, required modules present")
PY

echo "==> Sdist contents: src/cairn tree reproduces the wheel"
python3 - "$SDIST" "$WHEEL" <<'PY'
import sys
import tarfile
import zipfile

sdist, wheel = sys.argv[1], sys.argv[2]
tar = tarfile.open(sdist)
# Layout is <name>-<version>/src/cairn/...; derive the top dir dynamically.
top = next(name.split("/", 1)[0] for name in tar.getnames() if "/" in name)
prefix = f"{top}/src/cairn/"
sdist_cairn = {
    n[len(prefix):] for n in tar.getnames() if n.startswith(prefix)
}
wheel_cairn = {
    n[len("cairn/"):]
    for n in zipfile.ZipFile(wheel).namelist()
    if n.startswith("cairn/")
}
missing = sorted(wheel_cairn - sdist_cairn)
if missing:
    sys.exit(f"sdist cannot reproduce the wheel; missing: {missing}")
print(f"    sdist OK: {len(sdist_cairn)} entries under src/cairn cover all {len(wheel_cairn)} wheel entries")
PY

# Runs from an unrelated directory so `import cairn` resolves the installed
# artifact, never the source tree.
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$DIST_DIR" "$VENVS_DIR" "$WORKDIR"' EXIT

for artifact in "$WHEEL" "$SDIST"; do
    kind="wheel"
    [[ "$artifact" == *.tar.gz ]] && kind="sdist"
    venv="$VENVS_DIR/$kind"
    echo "==> Clean venv install from $kind: $(basename "$artifact")"
    uv venv --python python3 "$venv" >/dev/null
    # fsdantic depends on agentfs-sdk via a URL dependency (git). uv's pip
    # resolver refuses URL deps it must carry transitively, so pin the URL as
    # a *constraint* — exactly the resolution the project's uv.lock performs.
    constraint_file="$VENVS_DIR/fsdantic-constraints.txt"
    printf '%s\n' "agentfs-sdk @ $AGENTFS_URL" > "$constraint_file"
    uv pip install --python "$venv/bin/python" \
        --constraint "$constraint_file" \
        "fsdantic @ git+${FSDANTIC_URL}@${FSDANTIC_REF}" >/dev/null
    uv pip install --python "$venv/bin/python" \
        --constraint "$constraint_file" "$artifact" >/dev/null

    (
        cd "$WORKDIR"
        "$venv/bin/python" -c "import cairn; print('    import cairn OK (version', cairn.__version__ + ')')"
        "$venv/bin/cairn" --help >/dev/null && echo "    cairn --help OK"
        "$venv/bin/cairn-cli" --help >/dev/null && echo "    cairn-cli --help OK"
    )
done

echo "==> dist smoke test: all checks passed"
