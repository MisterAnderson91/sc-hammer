#!/usr/bin/env python3
"""Writes release notes (markdown) for a release or a preview build, to stdout.

Lists the commits since the last release that touch the app itself (sc_hammer/, packaging/);
README, docs and CI changes are left out. Adds install/upgrade steps and the AppImage checksum.

    release_notes.py --version 0.2.0 --appimage dist/SC-Hammer-0.2.0-x86_64.AppImage \\
        [--prev-tag v0.1.0] [--highlights "First point; second point"] [--preview]
"""
import argparse
import hashlib
import os
import re
import subprocess

APP_PATHS = ("sc_hammer/", "packaging/")
# Commits are grouped by a conventional prefix ("feat:", "fix:") or else by their first word.
GROUPS = [
    ("New", ("feat",), ("add", "adds", "added", "new", "support")),
    ("Fixes", ("fix",), ("fix", "fixes", "fixed", "stop", "prevent", "handle")),
]


def group_of(subject):
    m = re.match(r"^(\w+)(\(.+\))?!?:", subject)
    prefix = m.group(1).lower() if m else ""
    first = (clean(subject).split() or [""])[0].lower()
    for title, prefixes, words in GROUPS:
        if prefix in prefixes or first in words:
            return title
    return "Other changes"


def git(*args):
    return subprocess.run(["git"] + list(args), capture_output=True, text=True).stdout.strip()


def last_tag():
    return git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*") or None


def app_commits(prev_tag):
    rng = "%s..HEAD" % prev_tag if prev_tag else "HEAD"
    out = []
    for line in git("log", rng, "--no-merges", "--format=%H\x1f%s").splitlines():
        sha, subject = line.split("\x1f", 1)
        files = git("diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha).splitlines()
        if any(f.startswith(APP_PATHS) for f in files):
            out.append((sha[:7], subject.strip()))
    return out


def clean(subject):
    """"feat: add thing" -> "Add thing"."""
    s = re.sub(r"^\w+(\(.+\))?!?:\s*", "", subject)
    return s[:1].upper() + s[1:]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--appimage", required=True)
    ap.add_argument("--prev-tag")
    ap.add_argument("--highlights", default="")
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()

    prev = args.prev_tag or last_tag()
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    name = os.path.basename(args.appimage)
    out = []

    if args.preview:
        out += ["> **Preview build, not released.** Built from `%s`. This draft is replaced on every "
                "push to main. To release, run the **Release** workflow; don't publish this draft."
                % git("rev-parse", "--short", "HEAD"), ""]

    points = [p.strip() for p in re.split(r"[;\n]", args.highlights) if p.strip()]
    if points:
        out += ["## Highlights", ""] + ["- %s" % p for p in points] + [""]

    commits = app_commits(prev)
    grouped = {title: [] for title, _, _ in GROUPS}
    grouped["Other changes"] = []
    for sha, subject in commits:
        grouped[group_of(subject)].append("- %s (%s)" % (clean(subject), sha))
    if commits:
        out += ["## What's changed" + (" since %s" % prev if prev else ""), ""]
        for title, lines in grouped.items():
            if lines:
                out += ["**%s**" % title, ""] + lines + [""]
    elif prev:
        out += ["No app changes since %s." % prev, ""]

    out += [
        "## Install",
        "",
        "1. Download **`%s`** below." % name,
        "2. Make it executable: right-click it → Properties → Permissions → *Is executable*, or "
        "`chmod +x %s`." % name,
        "3. Double-click it. On SteamOS, use Desktop Mode (or add it as a non-Steam game).",
        "",
        "No sudo needed. If a receiver or controller doesn't show up, run it once with `--check`.",
        "",
        "## Upgrading",
        "",
        "Replace the old AppImage with this one. Your nicknames and backups are kept.",
        "",
        "## Checksum",
        "",
        "```",
        "%s  %s" % (sha256(args.appimage), name),
        "```",
    ]
    if repo and prev and not args.preview:
        out += ["", "**Full changelog:** https://github.com/%s/compare/%s...v%s"
                % (repo, prev, args.version)]
    print("\n".join(out))


if __name__ == "__main__":
    main()
