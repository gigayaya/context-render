"""Bash heuristics: git commit forms × file reads; each false negative documented via xfail."""

import pytest

from context_render.attributor import bash_heuristics as bh

COMMIT_OK = "[main abc1234] feat: x\n 2 files changed, 10 insertions(+)"


class TestGitCommit:
    def test_direct(self):
        assert bh.detect_git_commit("git commit", COMMIT_OK)

    def test_dash_m(self):
        assert bh.detect_git_commit('git commit -m "fix: y"', COMMIT_OK)

    def test_chained(self):
        assert bh.detect_git_commit("git add -A && git commit -m 'z'", COMMIT_OK)

    def test_c_prefix(self):
        assert bh.detect_git_commit("git -C /repo commit -m 'w'", COMMIT_OK)

    def test_non_commit_git(self):
        assert not bh.detect_git_commit("git status && git log", "on branch main")

    def test_requires_result_confirmation(self):
        # zero tolerance for false positives: without result confirmation it does not hold (AC2a)
        assert not bh.detect_git_commit("git commit -m 'x'", None)
        assert not bh.detect_git_commit("git commit -m 'x'", "error: nothing to commit")

    def test_commit_word_in_message_not_command(self):
        # "git commit" inside echo quotes: after shlex argv[0]=echo, no false match
        assert not bh.detect_git_commit('echo "please git commit later"', COMMIT_OK)


class TestReadPaths:
    def test_cat(self):
        assert bh.extract_read_paths("cat docs/a.md", "/repo") == ["/repo/docs/a.md"]

    def test_grep_skips_pattern(self):
        assert bh.extract_read_paths("grep TODO src/x.py", "/repo") == ["/repo/src/x.py"]

    def test_rg_skips_pattern(self):
        assert bh.extract_read_paths("rg -n 'foo' lib/y.py", "/repo") == ["/repo/lib/y.py"]

    def test_absolute(self):
        assert bh.extract_read_paths("head -5 /abs/f.txt", None) == ["/abs/f.txt"]

    def test_chain_segments(self):
        assert bh.extract_read_paths("ls && cat a.md | wc -l", "/r") == ["/r/a.md"]

    def test_non_read_cmd(self):
        assert bh.extract_read_paths("python run.py docs/a.md", "/r") == []

    # known false negatives (documented in README; documented via xfail, AC2b)
    @pytest.mark.xfail(reason="known false negative: redirection file read", strict=True)
    def test_miss_redirect(self):
        assert bh.extract_read_paths("wc -l < docs/a.md", "/r") == ["/r/docs/a.md"]

    @pytest.mark.xfail(reason="known false negative: xargs indirect file read", strict=True)
    def test_miss_xargs(self):
        assert bh.extract_read_paths("echo docs/a.md | xargs cat", "/r") == ["/r/docs/a.md"]

    @pytest.mark.xfail(reason="known false negative: sed/awk file read", strict=True)
    def test_miss_sed(self):
        assert bh.extract_read_paths("sed -n 1p docs/a.md", "/r") == ["/r/docs/a.md"]


class TestFalsePositiveRegression:
    """Real false-positive regression (example_project session 23:02): attached semicolon + redirection."""

    REAL_CMD = ('find . -maxdepth 3 -iname "venv" -o -iname ".venv" 2>/dev/null; '
                "ls data_source/insect/index.cjs 2>&1; "
                "python3.12 -m pip show pytest 2>&1 | head -3; "
                "pip3 list 2>/dev/null | grep -i pytest")

    def test_real_command_no_paths(self):
        # after correctly splitting the head segment on the semicolon there are no path args; pip3/list/2>/dev/null are no longer candidates
        assert bh.extract_read_paths(self.REAL_CMD, "/repo") == []

    def test_attached_semicolon_splits_segments(self):
        segs = bh.split_segments("head -3; pip3 list 2>/dev/null")
        assert ["head", "-3"] in segs
        assert ["pip3", "list", "2>/dev/null"] in segs

    def test_attached_semicolon_git_commit(self):
        # after splitting on the attached semicolon, chained commit is detected too (previously a false negative)
        assert bh.detect_git_commit("git add -A; git commit -m 'x'",
                                    "[main abc1234] x\n 1 file changed")

    def test_redirect_token_filtered_path_kept(self):
        assert bh.extract_read_paths("cat docs/a.md 2>/dev/null", "/r") == ["/r/docs/a.md"]

    def test_metachar_tokens_rejected(self):
        assert bh.extract_read_paths("cat $(find . -name x)", "/r") == []
        assert bh.extract_read_paths("cat *.md", "/r") == []


class TestValueFlagFalsePositive:
    """Regression (AB review): a grep/rg separate-value flag (-A 3, -m 1, rg -t py) whose
    value is not consumed shifts the real pattern into path position — and a pattern is
    often an existing file name, so it passed the is_file gate as a false read (AC2a)."""

    def test_after_context_value_does_not_shift_pattern(self):
        # CLAUDE.md is the pattern, not a read path — even though such a file exists
        assert bh.extract_read_paths("grep -n -A 3 CLAUDE.md notes.txt", "/repo") == ["/repo/notes.txt"]

    def test_max_count(self):
        assert bh.extract_read_paths("grep -m 1 TODO src/x.py", "/repo") == ["/repo/src/x.py"]

    def test_rg_type_flag(self):
        assert bh.extract_read_paths("rg -t py CLAUDE.md src/main.py", "/repo") == ["/repo/src/main.py"]

    def test_pattern_via_dash_e_makes_positionals_files(self):
        # -e supplies the pattern → the first positional is a file, not a pattern to skip
        assert bh.extract_read_paths("grep -e TODO src/x.py", "/repo") == ["/repo/src/x.py"]

    def test_posix_grep_dash_r_is_not_a_value_flag(self):
        """Regression: rg's -r takes a value (--replace) but POSIX grep's -r is flagless
        (recursive). Using the shared rg set for grep consumed the pattern as the flag's
        "value" and then dropped the real file as the presumed pattern — a false negative
        in one of the most common command shapes (facts.py already forked its set for
        exactly this hazard; extract_read_paths now picks per command head)."""
        assert bh.extract_read_paths("grep -r TODO notes.md", "/repo") == ["/repo/notes.md"]

    def test_rg_dash_r_still_consumes_its_replacement_value(self):
        # for rg, -r REPL must be consumed or REPL shifts into the pattern slot
        # and the real pattern becomes a candidate path
        assert bh.extract_read_paths("rg -r new old src/x.py", "/repo") == ["/repo/src/x.py"]

    def test_inline_value_unaffected(self):
        # inline forms stay inside the flag token; the positional skip still applies
        assert bh.extract_read_paths("grep -A3 TODO src/x.py", "/repo") == ["/repo/src/x.py"]


class TestNewlineSegmentation:
    """Regression: multi-line Bash is the norm in transcripts; an unquoted newline must
    separate commands like `;`, or later lines get harvested as args of the first (false
    positives — a `cp` write target credited as a file read)."""

    def test_newline_splits_segments(self):
        assert bh.split_segments("head -5 a.csv\ncp s.txt d.txt") == [
            ["head", "-5", "a.csv"],
            ["cp", "s.txt", "d.txt"],
        ]

    def test_no_false_positive_across_newline(self):
        # the second line's command and its write target must not become "reads" of head
        cmd = "head -5 data.csv\ncp src.txt dst.txt"
        assert bh.extract_read_paths(cmd, "/r") == ["/r/data.csv"]

    def test_read_on_a_later_line_detected(self):
        assert bh.extract_read_paths("ls\ncat docs/a.md", "/r") == ["/r/docs/a.md"]

    def test_git_commit_after_newline(self):
        assert bh.detect_git_commit("cd repo\ngit commit -m 'x'", COMMIT_OK)

    def test_quoted_newline_stays_inside_token(self):
        # a multi-line commit message is one argument, not a segment boundary
        assert bh.split_segments('git commit -m "a\nb"') == [["git", "commit", "-m", "a\nb"]]

    def test_separator_run_is_one_boundary(self):
        assert bh.split_segments("foo &&\nbar") == [["foo"], ["bar"]]


class TestGitSubcommandResolution:
    """How a git segment is read: global flags are skipped (value flags with their value),
    the first remaining token is the subcommand. Characterized here because three
    modules (commit detection, git grep facts, wildcard mutations) share this reading."""

    def test_valueless_global_flag_skipped(self):
        assert bh.detect_git_commit("git --no-pager commit -m 'x'", COMMIT_OK)

    def test_value_flag_consumes_its_value(self):
        # `-c key=val` takes a value: the value must not be read as the subcommand
        assert bh.detect_git_commit("git -c user.name=x commit -m 'y'", COMMIT_OK)

    def test_flags_only_is_not_a_commit(self):
        assert not bh.detect_git_commit("git -C /repo", COMMIT_OK)
        assert not bh.detect_git_commit("git", COMMIT_OK)

    def test_absolute_git_binary(self):
        assert bh.detect_git_commit("/usr/bin/git commit -m 'z'", COMMIT_OK)

    def test_flag_value_spelled_commit_is_not_the_subcommand(self):
        # the token after -C is a directory, even when it is spelled "commit"
        assert not bh.detect_git_commit("git -C commit status", COMMIT_OK)
