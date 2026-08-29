#!/usr/bin/env python3
"""
make_content_manifest.py
========================
Regenerates content/manifest.json from whatever is sitting in the content/
folder. Run by the "Build content manifest" GitHub Action after you add or
remove a file, then committed automatically -- so publishing on the static
site is just: drop the file in the right folder and commit.

Folder layout it expects
------------------------
    content/
      dsr/                       Daily DSR files, date in the filename
        DSR_29.08.2026.pdf
      memos/                     one sub-folder per subject
        Law and Order/
          circular.pdf
        Cyber Crime/
      it-apps/
        apps.json                hand-edited list of application links
      videos/
        road_safety.mp4
      <folder>/links.json        optional: files that live in Google Drive

Google Drive entries
--------------------
Any folder above may also carry a links.json listing files hosted on Drive:

    [
      { "name": "DSR 29.08.2026", "date": "2026-08-29",
        "drive": "1AbC2dEfGh3IjKlM" }
    ]

Give either "drive" (the Drive file ID) or a plain "url". Drive files must be
shared as "Anyone with the link - Viewer", otherwise visitors hit a request-
access page. Drive and repo files are merged into one list per section.

Output
------
content/manifest.json with four keys: dsr, memos, apps, videos.
The dashboard's District Resources tabs read this single file.
"""
import json
import os
import re
import subprocess
import urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))
CONTENT = os.path.join(ROOT, "content")

DOC_EXT = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
           ".png", ".jpg", ".jpeg", ".gif", ".webp", ".txt", ".csv")
VID_EXT = (".mp4", ".webm", ".ogg", ".mov", ".m4v")

# GitHub refuses a push containing a file larger than this.
HARD_LIMIT = 100 * 1024 * 1024
# Anything above this is worth a warning in the Action log.
WARN_LIMIT = 25 * 1024 * 1024


def git_date(relpath):
    """Last commit date for a file (ISO-8601), or None if unavailable."""
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--format=%cI", "--", relpath],
            cwd=ROOT, stderr=subprocess.DEVNULL,
        ).decode().strip()
        return out or None
    except Exception:
        return None


def url_for(relpath):
    """Repo-relative path -> URL the page can fetch, with each segment quoted."""
    return "/".join(urllib.parse.quote(p) for p in relpath.split("/"))


def date_from_name(fn):
    """Pull dd.mm.yyyy / dd-mm-yyyy / yyyy-mm-dd out of a filename."""
    base = os.path.splitext(fn)[0]
    m = re.search(r"(\d{1,2})[.\-_/ ](\d{1,2})[.\-_/ ](\d{4})", base)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            return "%04d-%02d-%02d" % (y, mo, d)
    m = re.search(r"(\d{4})[.\-_/ ](\d{1,2})[.\-_/ ](\d{1,2})", base)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            return "%04d-%02d-%02d" % (y, mo, d)
    return None


def entry(relpath, fn, exts, warnings):
    """Build one manifest entry, or None if the file should be skipped."""
    if fn.startswith(".") or fn.lower() == "manifest.json":
        return None
    if not fn.lower().endswith(exts):
        return None
    full = os.path.join(ROOT, relpath)
    if not os.path.isfile(full):
        return None
    size = os.path.getsize(full)
    if size > HARD_LIMIT:
        warnings.append("TOO LARGE (GitHub rejects >100 MB): %s" % relpath)
    elif size > WARN_LIMIT:
        warnings.append("large file (%.1f MB): %s" % (size / 1048576.0, relpath))
    return {
        "name": fn,
        "url": url_for(relpath),
        "size": size,
        "date": date_from_name(fn) or git_date(relpath),
    }


def drive_entry(rec, warnings):
    """One entry from a links.json record (Drive-hosted or plain URL)."""
    name = rec.get("name") or rec.get("title")
    if not name:
        warnings.append("links.json entry with no name - skipped")
        return None
    fid = rec.get("drive") or rec.get("id")
    if fid:
        url = "https://drive.google.com/file/d/%s/view" % fid
        entry_out = {
            "name": name,
            "url": url,
            "embed": "https://drive.google.com/file/d/%s/preview" % fid,
            "download": "https://drive.google.com/uc?export=download&id=%s" % fid,
            "source": "drive",
        }
    elif rec.get("url"):
        entry_out = {"name": name, "url": rec["url"], "source": "link"}
    else:
        warnings.append("links.json entry '%s' has no drive id or url" % name)
        return None
    entry_out["size"] = rec.get("size")
    entry_out["date"] = rec.get("date") or date_from_name(name)
    return entry_out


def load_links(folder, warnings):
    """content/<folder>/links.json -> list of Drive / external entries."""
    p = os.path.join(CONTENT, folder, "links.json")
    if not os.path.isfile(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        warnings.append("could not read %s/links.json -- %s" % (folder, exc))
        return []
    if not isinstance(data, list):
        warnings.append("%s/links.json is not a JSON list" % folder)
        return []
    out = []
    for rec in data:
        if isinstance(rec, dict):
            e = drive_entry(rec, warnings)
            if e:
                out.append(e)
    return out


def scan_flat(folder, exts, warnings):
    """Every allowed file directly inside content/<folder>/."""
    d = os.path.join(CONTENT, folder)
    if not os.path.isdir(d):
        return load_links(folder, warnings)
    out = []
    for fn in sorted(os.listdir(d)):
        e = entry("content/%s/%s" % (folder, fn), fn, exts, warnings)
        if e:
            e["source"] = "repo"
            out.append(e)
    out.extend(load_links(folder, warnings))
    out.sort(key=lambda x: (x["date"] or ""), reverse=True)
    return out


def scan_memos(warnings):
    """One group per sub-folder of content/memos/."""
    d = os.path.join(CONTENT, "memos")
    if not os.path.isdir(d):
        return []
    groups = []
    for subject in sorted(os.listdir(d)):
        sub = os.path.join(d, subject)
        if not os.path.isdir(sub) or subject.startswith("."):
            continue
        files = []
        for fn in sorted(os.listdir(sub)):
            e = entry("content/memos/%s/%s" % (subject, fn), fn,
                      DOC_EXT + VID_EXT, warnings)
            if e:
                e["source"] = "repo"
                files.append(e)
        files.extend(load_links("memos/%s" % subject, warnings))
        files.sort(key=lambda x: (x["date"] or ""), reverse=True)
        groups.append({"subject": subject, "files": files})
    return groups


def load_apps():
    p = os.path.join(CONTENT, "it-apps", "apps.json")
    if not os.path.isfile(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as exc:
        print("WARNING: could not read apps.json -- %s" % exc)
        return []


def main():
    if not os.path.isdir(CONTENT):
        print("No content/ folder found -- nothing to do.")
        return
    warnings = []
    manifest = {
        "dsr": scan_flat("dsr", DOC_EXT, warnings),
        "memos": scan_memos(warnings),
        "apps": load_apps(),
        "videos": scan_flat("videos", VID_EXT, warnings),
    }
    out = os.path.join(CONTENT, "manifest.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print("Wrote %s" % out)
    print("  DSR      : %d file(s)" % len(manifest["dsr"]))
    print("  Memos    : %d subject(s), %d file(s)"
          % (len(manifest["memos"]),
             sum(len(g["files"]) for g in manifest["memos"])))
    print("  Apps     : %d link(s)" % len(manifest["apps"]))
    print("  Videos   : %d file(s)" % len(manifest["videos"]))
    drive_n = sum(1 for x in manifest["dsr"] + manifest["videos"]
                  if x.get("source") == "drive")
    drive_n += sum(1 for g in manifest["memos"] for x in g["files"]
                   if x.get("source") == "drive")
    print("  (of which %d hosted on Google Drive)" % drive_n)
    for w in warnings:
        print("  ! " + w)


if __name__ == "__main__":
    main()
