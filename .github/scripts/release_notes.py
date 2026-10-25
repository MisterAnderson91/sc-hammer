#!/usr/bin/env python3
"""Writes release notes (markdown) for a release or a preview build, to stdout.

Lists the commits since the last release that touch the app itself (sc_hammer/, packaging/);
README, docs and CI changes are left out. Every release in a series (e.g. 0.1.x) also gets the
series summary from .github/release-notes/<series>.md and the notes of the earlier releases in that
series (fetched with gh), then install/upgrade steps and checksums.

    release_notes.py --version 0.2.0 --asset dist/SC-Hammer-x86_64.AppImage \\
        --asset dist/SC-Hammer-0.2.0-windows-x64.exe \\
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


def series_of(version):
    return ".".join(version.split("-")[0].split(".")[:2])


def _version_key(tag):
    try:
        return tuple(int(x) for x in tag.lstrip("v").split("-")[0].split("."))
    except ValueError:
        return ()


def earlier_in_series(version):
    """Notes of the published releases in the same x.y series before this one, newest first.
    Each is (tag, its highlights and changes), with headings pushed down a level."""
    series = series_of(version)
    current = _version_key(version)
    tags = [t for t in git("tag", "-l", "v%s.*" % series).splitlines()
            if _version_key(t) and _version_key(t) < current]
    out = []
    for tag in sorted(tags, key=_version_key, reverse=True):
        body = subprocess.run(["gh", "release", "view", tag, "--json", "body", "--jq", ".body"],
                              capture_output=True, text=True).stdout
        # Keep only that release's own highlights and changes.
        for marker in ("## What's in", "## Earlier", "## Install"):
            body = body.split(marker)[0]
        body = body.strip()
        if body:
            out.append((tag, re.sub(r"(?m)^## ", "#### ", body)))
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
    ap.add_argument("--asset", action="append", required=True,
                    help="a release file (the AppImage, the Windows .exe); repeat for each")
    ap.add_argument("--prev-tag")
    ap.add_argument("--highlights", default="")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--branch", default="main")
    args = ap.parse_args()

    prev = args.prev_tag or last_tag()
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    names = [os.path.basename(a) for a in args.asset]
    appimage = next((n for n in names if n.endswith(".AppImage")), None)
    exe = next((n for n in names if n.endswith(".exe")), None)
    out = []

    if args.preview:
        out += ["> **Preview build, not released.** Built from `%s` on `%s`. This draft is replaced "
                "on every push to that branch. To release, run the **Release** workflow; don't "
                "publish this draft." % (git("rev-parse", "--short", "HEAD"), args.branch), ""]

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

    series = series_of(args.version)
    summary = os.path.join(os.path.dirname(__file__), "..", "release-notes", "%s.md" % series)
    if os.path.exists(summary):
        out += ["## What's in SC Hammer %s" % series, "", open(summary).read().strip(), ""]
    earlier = [] if args.preview else earlier_in_series(args.version)
    if earlier:
        out += ["## Earlier %s releases" % series, ""]
        for tag, body in earlier:
            out += ["### %s" % tag, "", body, ""]

    out += ["## Install", ""]
    if appimage:
        out += [
            "**Linux / SteamOS:** download **`%s`**, make it executable (right-click → Properties → "
            "Permissions → *Is executable*, or `chmod +x %s`), then double-click it. On SteamOS, "
            "use Desktop Mode (or add it as a non-Steam game). No sudo needed; if a receiver or "
            "controller doesn't show up, run it once with `--check`." % (appimage, appimage), ""]
    if exe:
        out += [
            "**Windows 10/11:** download **`%s`** and double-click it. Nothing to install. Windows "
            "may say *\"Windows protected your PC\"* because the app isn't code-signed: click "
            "**More info → Run anyway**." % exe, ""]
    out += [
        "## Upgrading",
        "",
        "Replace the old file with the new one. Your nicknames and backups are kept.",
        "",
        "## Checksums (SHA-256)",
        "",
        "```",
    ] + ["%s  %s" % (sha256(a), os.path.basename(a)) for a in args.asset] + ["```"]
    if repo and prev and not args.preview:
        out += ["", "**Full changelog:** https://github.com/%s/compare/%s...v%s"
                % (repo, prev, args.version)]
    print("\n".join(out))


if __name__ == "__main__":
    main()
