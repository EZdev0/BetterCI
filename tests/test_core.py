import json
import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(__file__))
import mock_llm  # noqa: E402
from vibeci import cli, diffmap, gate  # noqa: E402
from vibeci.llm import LLMClient  # noqa: E402
from vibeci.models import FixProposal, Suggestion  # noqa: E402
from vibeci.review import render_body, to_comments  # noqa: E402

DIFF = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,3 +1,4 @@
 import os
+import sys
 x = 1
-y = 2
+y = 3
"""


def test_diffmap_visible_lines():
    assert diffmap.parse_diff(DIFF) == {"a.py": {1, 2, 3, 4}}


def test_diffmap_rejects_unsafe_ref():
    with pytest.raises(ValueError):
        diffmap.git_diff("main; rm -rf /")


def test_proposal_rules():
    ok = dict(verdict="fix", bug="bug here", start_line=1, end_line=1, replacement="x", confidence="high")
    FixProposal(**ok)
    with pytest.raises(ValidationError):
        FixProposal(**{**ok, "replacement": None})
    with pytest.raises(ValidationError):
        FixProposal(**{**ok, "verdict": "no_fix"})
    with pytest.raises(ValidationError):
        FixProposal(**{**ok, "end_line": 0})
    with pytest.raises(ValidationError):
        FixProposal(**{**ok, "extra_field": 1})


def test_gate_rejects_syntax_and_noop():
    src = "def f():\n    return 1\n"
    assert not gate.check_patched("a.py", src, "def f(:\n").ok
    assert not gate.check_patched("a.py", src, src).ok
    assert gate.check_patched("a.py", src, "def f():\n    return 2\n").ok
    assert not gate.check_patched("a.sh", "echo hi\n", "if then\n").ok
    assert not gate.check_patched("a.bin", "a", "b").ok


def test_apply_replacement_edges():
    assert gate.apply_replacement("a\nb\nc\n", 2, 2, "B") == "a\nB\nc\n"
    assert gate.apply_replacement("a\nb\nc", 3, 3, "C") == "a\nb\nC"
    assert gate.apply_replacement("a\nb\nc\n", 2, 3, "") == "a\n"
    with pytest.raises(ValueError):
        gate.apply_replacement("a\n", 2, 2, "x")


def test_render_uses_longer_fence_and_strips_html_and_mentions():
    s = Suggestion(path="a.py", start_line=2, end_line=3, replacement="x = '```'\n", title="<b>t</b> @alice",
                   detail="d <script>x</script>", source="llm", rule="SIM115", confidence="medium")
    body = render_body(s)
    assert "````suggestion" in body and "<script>" not in body and "@alice" not in body
    c = to_comments([s])[0]
    assert (c["start_line"], c["line"], c["side"]) == (2, 3, "RIGHT")


def test_llm_url_must_be_local():
    with pytest.raises(ValueError):
        LLMClient("https://api.example.com")


# ---------------------------------------------------------------- e2e -----
BASE = "import json\n\n\ndef load(path):\n    return 1\n"
HEAD = ("import json\nimport os\n\n\ndef load(path):\n    f = open(path)\n    data = json.load(f)\n    return data\n")


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    (tmp_path / "app.py").write_text(BASE); git("add", "-A"); git("commit", "-qm", "base")
    (tmp_path / "app.py").write_text(HEAD); git("commit", "-qam", "head")
    monkeypatch.chdir(tmp_path)
    return tmp_path


GOOD = {"verdict": "fix", "bug": "File handle is never closed.", "start_line": 6, "end_line": 7,
        "replacement": "    with open(path) as f:\n        data = json.load(f)\n", "confidence": "high",
        "edge_case": {"kind": "resource_leak", "scenario": "json.load raises, handle stays open until GC."}}


@pytest.mark.parametrize("transport", ["anthropic", "grammar"])
def test_e2e_tool_fix_plus_llm_fix(repo, transport):
    srv, url = mock_llm.start()
    mock_llm.REPLY["value"] = GOOD
    rc = cli.main(["scan", "--base", "HEAD~1", "--llm-url", url, "--transport", transport, "--out", "out"])
    assert rc == 0
    sugs = json.load(open("out/review.json"))["suggestions"]
    by_src = {s["source"]: s for s in sugs}
    assert "tool" in by_src and "import os" not in by_src["tool"]["replacement"]  # F401 removed deterministically
    assert by_src["llm"]["replacement"].lstrip().startswith("with open")
    patched = gate.apply_replacement(HEAD, by_src["llm"]["start_line"], by_src["llm"]["end_line"], by_src["llm"]["replacement"])
    compile(patched, "app.py", "exec")
    srv.shutdown()


@pytest.mark.parametrize("bad", [
    {**GOOD, "start_line": 1, "end_line": 2},                                 # outside window? inside window but not the bug -> still must pass gate
    {**GOOD, "replacement": "    with open(path) as f\n        data = 1\n"},  # syntax error -> gate
    {**GOOD, "replacement": "   6|     with open(path) as f:\n"},              # line-number prefix
    {**GOOD, "start_line": 500, "end_line": 501},                               # outside window
    {"verdict": "fix", "bug": "x"},                                            # schema invalid
])
def test_e2e_bad_model_output_never_posts_llm_fix(repo, bad):
    srv, url = mock_llm.start()
    mock_llm.REPLY["value"] = bad
    cli.main(["scan", "--base", "HEAD~1", "--llm-url", url, "--out", "out"])
    sugs = json.load(open("out/review.json"))["suggestions"]
    for s in sugs:
        if s["source"] == "llm":
            patched = gate.apply_replacement(HEAD, s["start_line"], s["end_line"], s["replacement"])
            compile(patched, "app.py", "exec")  # whatever is posted must at least compile
    assert not any(s["source"] == "llm" and s["replacement"].startswith("   6|") for s in sugs)
    srv.shutdown()


def test_no_llm_mode_is_deterministic(repo):
    cli.main(["scan", "--base", "HEAD~1", "--no-llm", "--out", "out"])
    sugs = json.load(open("out/review.json"))["suggestions"]
    assert sugs and all(s["source"] == "tool" for s in sugs)
