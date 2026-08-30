"""Bash stale-gauge heuristics (upgrade-plan §2.2/§2.4; design §2.2).

Asserted misses are deliberate (zero-false-positive priority, AC2a):
grep/rg are searches not whole-file reads, metachar tokens are never paths.
"""

from __future__ import annotations

from context_render.attributor.bash_mutations import (
    extract_mutations,
    extract_stale_reads,
)

CWD = "/repo"


# ---- reads ----

def test_read_cat_relative_path_absolutized():
    assert extract_stale_reads("cat docs/a.md", CWD) == ["/repo/docs/a.md"]


def test_read_head_tail_less_more():
    cmd = "head x.md; tail y.md && less z.md | more w.md"
    assert extract_stale_reads(cmd, CWD) == [
        "/repo/x.md", "/repo/y.md", "/repo/z.md", "/repo/w.md"]


def test_read_head_value_flag_not_harvested_as_path():
    assert extract_stale_reads("head -n 50 f.txt", CWD) == ["/repo/f.txt"]
    assert extract_stale_reads("tail -c 100 f.txt", CWD) == ["/repo/f.txt"]


def test_read_sed_n_is_a_read_sed_i_is_not():
    assert extract_stale_reads("sed -n '1,50p' f.txt", CWD) == ["/repo/f.txt"]
    assert extract_stale_reads("sed -i 's/a/b/' f.txt", CWD) == []


def test_read_grep_rg_are_not_reads():
    assert extract_stale_reads("grep foo f.txt", CWD) == []
    assert extract_stale_reads("rg foo f.txt", CWD) == []


def test_read_metachar_token_never_a_path():
    assert extract_stale_reads("cat *.md", CWD) == []
    assert extract_stale_reads("cat $(ls)", CWD) == []


def test_read_unparseable_returns_nothing():
    assert extract_stale_reads("cat 'unterminated", CWD) == []


def test_read_dotdot_path_normalized():
    assert extract_stale_reads("cat docs/../a.md", CWD) == ["/repo/a.md"]


# ---- targeted mutations ----

def test_mut_redirect_separate_and_attached_token():
    t, w = extract_mutations("echo hi > out.md", CWD)
    assert t == [("/repo/out.md", "echo")] and w == []
    t, _ = extract_mutations("echo hi >>log.txt", CWD)
    assert t == [("/repo/log.txt", "echo")]


def test_mut_redirect_dev_null_and_fd_dup_excluded():
    assert extract_mutations("cmd > /dev/null", CWD) == ([], [])
    assert extract_mutations("cmd 2>/dev/null", CWD) == ([], [])
    assert extract_mutations("cmd 1>&2", CWD) == ([], [])


def test_mut_sed_i_targets_files_not_script():
    t, _ = extract_mutations("sed -i 's/a/b/' f.txt g.txt", CWD)
    assert t == [("/repo/f.txt", "sed"), ("/repo/g.txt", "sed")]


def test_mut_tee_and_mv_rm():
    assert extract_mutations("echo x | tee -a f.txt", CWD)[0] == [("/repo/f.txt", "tee")]
    assert extract_mutations("mv a.md b.md", CWD)[0] == [
        ("/repo/a.md", "mv"), ("/repo/b.md", "mv")]
    assert extract_mutations("rm -f a.md", CWD)[0] == [("/repo/a.md", "rm")]


def test_mut_grep_and_sed_n_do_not_mutate():
    assert extract_mutations("grep foo f.txt", CWD) == ([], [])
    assert extract_mutations("sed -n '1p' f.txt", CWD) == ([], [])


# ---- wildcard mutations ----

def test_wildcard_git_commands():
    for cmd, label in [("git pull", "git pull"), ("git merge main", "git merge"),
                       ("git rebase main", "git rebase"), ("git restore .", "git restore"),
                       ("git checkout main", "git checkout"),
                       ("git stash pop", "git stash pop")]:
        assert extract_mutations(cmd, CWD)[1] == [label], cmd


def test_wildcard_git_checkout_b_creates_branch_not_files():
    assert extract_mutations("git checkout -b feat/x", CWD) == ([], [])


def test_wildcard_git_global_flags_skipped():
    assert extract_mutations("git -C /repo pull", CWD)[1] == ["git pull"]


def test_git_commit_is_not_a_mutation():
    assert extract_mutations("git add -A && git commit -m 'x'", CWD) == ([], [])
