"""The slug rule, forwards and backwards."""

from __future__ import annotations

import pytest

from claude_session_rescue import slugs


@pytest.mark.parametrize("path,expected", [
    # The rule as verified against a real Windows store: the drive colon and the
    # separator after it give the leading "--", and a space inside a directory
    # name collapses like any other separator.
    (r"C:\Users\alex smith\code\my_project", "C--Users-alex-smith-code-my-project"),
    # Same rule, other platforms.
    ("/home/ana/code/my_project", "-home-ana-code-my-project"),
    ("/Users/sam/Developer/side project", "-Users-sam-Developer-side-project"),
    # Dots collapse too, which is why worktree slugs contain "--".
    ("/home/ana/proj/.claude-worktrees/feat-abc123",
     "-home-ana-proj--claude-worktrees-feat-abc123"),
    # A literal dash survives, indistinguishable from a separator.
    ("/home/ana/my-app", "-home-ana-my-app"),
    # Trailing separators are dropped rather than becoming a trailing dash.
    ("/home/ana/code/", "-home-ana-code"),
])
def test_slug_for_path(path, expected):
    assert slugs.slug_for_path(path) == expected


def test_encoding_is_lossy_by_design():
    """Different paths can share a slug. Anything decoding must allow for it."""
    assert slugs.slug_for_path("/a/b_c") == slugs.slug_for_path("/a/b c")
    assert slugs.slug_for_path("/a/b-c") == slugs.slug_for_path("/a/b/c")


def test_worktree_detection_and_parent():
    slug = "-home-ana-proj--claude-worktrees-feat-abc123"
    assert slugs.looks_like_worktree(slug)
    assert slugs.worktree_parent_slug(slug) == "-home-ana-proj"
    assert slugs.worktree_name(slug) == "feat-abc123"


def test_non_worktree_has_no_parent():
    assert not slugs.looks_like_worktree("-home-ana-proj")
    assert slugs.worktree_parent_slug("-home-ana-proj") is None


def test_path_from_sessions_is_exact():
    """The cwd recorded inside a transcript resolves the ambiguity exactly."""
    slug = "-home-ana-code-my-project"
    # The recorded cwd is a *subdirectory* of the session root, as seen in real
    # files, so the resolver has to walk upwards.
    cwds = ["/home/ana/code/my_project/src/deep", "/home/ana/code/my_project"]
    assert slugs.path_from_sessions(cwds, slug) == "/home/ana/code/my_project"


def test_path_from_sessions_gives_up_cleanly():
    assert slugs.path_from_sessions(["/somewhere/else"], "-not-this-one") is None
    assert slugs.path_from_sessions([], "-anything") is None


def test_decode_probes_the_real_filesystem(tmp_path):
    """Probing should recover a path containing both a space and an underscore."""
    target = tmp_path / "my project" / "sub_dir"
    target.mkdir(parents=True)
    slug = slugs.slug_for_path(target)
    decoded, confidence = slugs.decode_slug(slug, probe=True)
    assert confidence == "probed"
    assert decoded == str(target)


def test_decode_falls_back_to_a_labelled_guess():
    decoded, confidence = slugs.decode_slug("-nowhere-at-all-really", probe=True)
    assert confidence == "guess"
    assert decoded.startswith("/")
