"""Module entry point so ``python -m agents_md_compiler`` matches the console script."""

from agents_md_compiler.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
