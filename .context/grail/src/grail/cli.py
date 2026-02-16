"""Command-line interface for grail."""

import argparse
import sys
import json
import inspect
from pathlib import Path
from typing import List

from grail.script import load
from grail.artifacts import ArtifactsManager
from grail.errors import GrailError, ParseError
from grail.limits import DEFAULT


def cmd_init(args):
    """Initialize grail project."""
    try:
        grail_dir = Path(".grail")
        grail_dir.mkdir(exist_ok=True)

        # Add to .gitignore if it exists
        gitignore = Path(".gitignore")
        if gitignore.exists():
            content = gitignore.read_text()
            if ".grail/" not in content:
                with gitignore.open("a") as f:
                    f.write("\n# Grail artifacts\n.grail/\n")
                print("✓ Added .grail/ to .gitignore")

        # Create sample .pym file
        sample_pym = Path("example.pym")
        if not sample_pym.exists():
            sample_pym.write_text("""from grail import external, Input
from typing import Any

# Declare inputs
name: str = Input("name")

# Declare external functions
@external
async def greet(name: str) -> str:
    '''Generate a greeting message.'''
    ...

# Execute
message = await greet(name)
{"greeting": message}
""")
            print("✓ Created example.pym")

        print("\n✓ Grail initialized!")
        print("\nNext steps:")
        print("  1. Edit example.pym")
        print("  2. Run: grail check example.pym")
        print("  3. Create a host file and run: grail run example.pym --host host.py")
        return 0
    except ParseError as e:
        if getattr(args, "verbose", False):
            raise
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except GrailError as e:
        if getattr(args, "verbose", False):
            raise
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        if getattr(args, "verbose", False):
            raise
        filename = e.filename or str(e)
        print(f"Error: File not found: {filename}", file=sys.stderr)
        return 1


def cmd_check(args):
    """Check .pym files for Monty compatibility."""
    try:
        # Find files to check
        if args.files:
            files = [Path(f) for f in args.files]
        else:
            # Find all .pym files recursively
            files = list(Path.cwd().rglob("*.pym"))

        if not files:
            print("No .pym files found")
            return 1

        results = []
        for file_path in files:
            script = load(file_path, grail_dir=None)
            result = script.check()
            results.append((file_path, result))

        # Output results
        if args.format == "json":
            # JSON output for CI
            output = []
            for file_path, result in results:
                output.append(
                    {
                        "file": str(file_path),
                        "valid": result.valid,
                        "errors": [
                            {
                                "line": e.lineno,
                                "column": e.col_offset,
                                "code": e.code,
                                "message": e.message,
                                "suggestion": e.suggestion,
                            }
                            for e in result.errors
                        ],
                        "warnings": [
                            {
                                "line": w.lineno,
                                "column": w.col_offset,
                                "code": w.code,
                                "message": w.message,
                            }
                            for w in result.warnings
                        ],
                        "info": result.info,
                    }
                )
            print(json.dumps(output, indent=2))
        else:
            # Human-readable output
            passed = 0
            failed = 0

            for file_path, result in results:
                if result.valid and (not args.strict or not result.warnings):
                    print(
                        f"{file_path}: OK ({result.info['externals_count']} externals, "
                        f"{result.info['inputs_count']} inputs, "
                        f"{len(result.errors)} errors, {len(result.warnings)} warnings)"
                    )
                    passed += 1
                else:
                    print(f"{file_path}: FAIL")
                    failed += 1

                    for error in result.errors:
                        print(
                            f"  {file_path}:{error.lineno}:{error.col_offset}: "
                            f"{error.code} {error.message}"
                        )

                    if args.strict:
                        for warning in result.warnings:
                            print(
                                f"  {file_path}:{warning.lineno}:{warning.col_offset}: "
                                f"{warning.code} {warning.message}"
                            )

            print(f"\nChecked {len(files)} files: {passed} passed, {failed} failed")

            if failed > 0:
                return 1

        return 0
    except ParseError as e:
        if getattr(args, "verbose", False):
            raise
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except GrailError as e:
        if getattr(args, "verbose", False):
            raise
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        if getattr(args, "verbose", False):
            raise
        filename = e.filename or str(e)
        print(f"Error: File not found: {filename}", file=sys.stderr)
        return 1


def cmd_run(args):
    """Run a .pym file with a host file."""
    import asyncio
    import importlib.util

    try:
        # Load the .pym script
        script_path = Path(args.file)
        if not script_path.exists():
            print(f"Error: {script_path} not found", file=sys.stderr)
            return 1

        inputs = {}
        for item in args.input:
            if "=" not in item:
                print(
                    f"Error: Invalid input format '{item}'. Use key=value.",
                    file=sys.stderr,
                )
                return 1
            key, value = item.split("=", 1)
            inputs[key.strip()] = value.strip()

        # Load host file if provided
        if args.host:
            host_path = Path(args.host)
            if not host_path.exists():
                print(f"Error: Host file {host_path} not found", file=sys.stderr)
                return 1

            # Import host module
            spec = importlib.util.spec_from_file_location("host", host_path)
            host_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(host_module)

            # Run host's main()
            if hasattr(host_module, "main"):
                main_fn = host_module.main
                call_args = ()
                call_kwargs = {}
                if inputs:
                    try:
                        signature = inspect.signature(main_fn)
                    except (TypeError, ValueError):
                        call_args = (inputs,)
                    else:
                        parameters = signature.parameters
                        accepts_var_kw = any(
                            param.kind == inspect.Parameter.VAR_KEYWORD
                            for param in parameters.values()
                        )
                        accepts_var_pos = any(
                            param.kind == inspect.Parameter.VAR_POSITIONAL
                            for param in parameters.values()
                        )
                        if "inputs" in parameters or accepts_var_kw:
                            call_kwargs = {"inputs": inputs}
                        elif accepts_var_pos or parameters:
                            call_args = (inputs,)
                        else:
                            print(
                                "Error: Host main() does not accept inputs. "
                                "Remove --input or update its signature.",
                                file=sys.stderr,
                            )
                            return 1

                if asyncio.iscoroutinefunction(main_fn):
                    asyncio.run(main_fn(*call_args, **call_kwargs))
                else:
                    main_fn(*call_args, **call_kwargs)
            else:
                print("Error: Host file must define a main() function", file=sys.stderr)
                return 1
        else:
            print("Error: --host <host.py> is required", file=sys.stderr)
            return 1

        return 0
    except ParseError as e:
        if getattr(args, "verbose", False):
            raise
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except GrailError as e:
        if getattr(args, "verbose", False):
            raise
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        if getattr(args, "verbose", False):
            raise
        filename = e.filename or str(e)
        print(f"Error: File not found: {filename}", file=sys.stderr)
        return 1


def cmd_watch(args):
    """Watch .pym files and re-run check on changes."""
    try:
        import watchfiles
    except ImportError:
        print(
            "Error: 'grail watch' requires the watchfiles package.\n"
            "Install it with: pip install grail[watch]",
            file=sys.stderr,
        )
        return 1

    watch_dir = Path(args.dir) if args.dir else Path.cwd()

    print(f"Watching {watch_dir} for .pym file changes...")
    print("Press Ctrl+C to stop")

    # Initial check
    print("\n=== Initial check ===")
    cmd_check(argparse.Namespace(files=None, format="text", strict=False))

    # Watch for changes
    for changes in watchfiles.watch(watch_dir, recursive=True):
        # Filter for .pym files
        pym_changes = [c for c in changes if c[1].endswith(".pym")]
        if pym_changes:
            print(f"\n=== Changes detected ===")
            cmd_check(argparse.Namespace(files=None, format="text", strict=False))


def cmd_clean(args):
    """Remove .grail/ directory."""
    try:
        grail_dir = Path(".grail")

        if grail_dir.exists():
            mgr = ArtifactsManager(grail_dir)
            mgr.clean()
            print("✓ Removed .grail/")
        else:
            print(".grail/ does not exist")

        return 0
    except ParseError as e:
        if getattr(args, "verbose", False):
            raise
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except GrailError as e:
        if getattr(args, "verbose", False):
            raise
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        if getattr(args, "verbose", False):
            raise
        filename = e.filename or str(e)
        print(f"Error: File not found: {filename}", file=sys.stderr)
        return 1


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Grail - Transparent Python for Monty", prog="grail"
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show full error tracebacks",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # grail init
    parser_init = subparsers.add_parser("init", help="Initialize grail project")
    parser_init.set_defaults(func=cmd_init)

    # grail check
    parser_check = subparsers.add_parser("check", help="Check .pym files")
    parser_check.add_argument("files", nargs="*", help=".pym files to check")
    parser_check.add_argument(
        "--format", choices=["text", "json"], default="text", help="Output format"
    )
    parser_check.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    parser_check.set_defaults(func=cmd_check)

    # grail run
    parser_run = subparsers.add_parser("run", help="Run a .pym file")
    parser_run.add_argument("file", help=".pym file to run")
    parser_run.add_argument("--host", help="Host Python file with main() function")
    parser_run.add_argument(
        "--input",
        "-i",
        action="append",
        default=[],
        help="Input value as key=value (can be repeated)",
    )
    parser_run.set_defaults(func=cmd_run)

    # grail watch
    parser_watch = subparsers.add_parser("watch", help="Watch and check .pym files")
    parser_watch.add_argument("dir", nargs="?", help="Directory to watch")
    parser_watch.set_defaults(func=cmd_watch)

    # grail clean
    parser_clean = subparsers.add_parser("clean", help="Remove .grail/ directory")
    parser_clean.set_defaults(func=cmd_clean)

    # Parse and execute
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
