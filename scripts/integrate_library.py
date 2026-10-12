"""Reapply the library navigation after regenerating this static Hugo export.

Idempotent, preserves unrelated content, and keeps adjacent backups before edits.
This repository contains generated HTML; use this after copying a new Hugo build.
"""
from pathlib import Path
import json
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
NAV = '''            <li>
                <a href="/library/" title="📚 Preprint Library">
                    <span>📚 Library</span>
                </a>
            </li>
'''
BUTTON = '''            <a class="button" href="/library/" title="Preprint Library">
                <span class="button-inner">Preprint Library</span>
            </a>
'''


def write_with_backup(path, content):
    if content == path.read_bytes().decode("utf-8"):
        return False
    backup = path.with_name(path.name + ".pre-library.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(content, encoding="utf-8", newline="")
    return True


def main():
    changed = []
    for path in ROOT.rglob("*.html"):
        relative = path.relative_to(ROOT)
        if relative.parts[0] in {".git", ".local", "library"}:
            continue
        content = path.read_bytes().decode("utf-8")
        menu = re.search(r'<ul id="menu">.*?</ul>', content, re.S)
        if menu and "/library/" not in menu.group():
            # Insert before the final search item so navigation stays familiar.
            target = re.search(r'(?m)^ {12}<li>\r?\n {16}<a[^>]+title="🔍 Search', menu.group())
            if target:
                at = menu.start() + target.start()
                content = content[:at] + NAV + content[at:]
        if relative.as_posix() == "index.html":
            buttons = re.search(r'<div class="buttons">.*?</div>', content, re.S)
            if buttons and "/library/" not in buttons.group():
                at = buttons.end() - len("</div>")
                content = content[:at] + BUTTON + "        " + content[at:]
        if write_with_backup(path, content):
            changed.append(relative.as_posix())
    index = ROOT / "index.json"
    entries = json.loads(index.read_text(encoding="utf-8"))
    library_url = "https://RLDMJC.github.io/library/"
    if not any(e.get("permalink") == library_url for e in entries):
        entries.insert(0, {
            "title": "Preprint Library",
            "permalink": library_url,
            "summary": "Recent research on learning, cognition, and AI",
            "content": "Search recent arXiv, PsyArXiv, and bioRxiv preprints on reinforcement learning in humans and animals, AI for cognitive science and psychology, and AI methods with implications for human learning. Filter topics and sources, browse abstracts, and save papers and authors for future speaker tracking.",
        })
        write_with_backup(index, json.dumps(entries, ensure_ascii=False, separators=(",", ":")))
        changed.append("index.json")
    print(f"Integrated library into {len(changed)} files.")
    for name in changed:
        print(name)


if __name__ == "__main__":
    main()
