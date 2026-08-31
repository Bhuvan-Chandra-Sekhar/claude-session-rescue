"""claude-session-rescue: read-only rescue tools for Claude Code session transcripts.

Hard invariant for the whole package: nothing under ``~/.claude`` is ever opened
for writing.  See :mod:`claude_session_rescue.safety`.
"""

__version__ = "0.1.0"
