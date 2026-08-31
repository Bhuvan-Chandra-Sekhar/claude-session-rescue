"""Allow ``python -m claude_session_rescue`` as well as the console script."""

from claude_session_rescue.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
