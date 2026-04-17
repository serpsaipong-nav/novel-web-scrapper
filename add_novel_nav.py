#!/usr/bin/env python3
"""
Add navigation (prev/next/index) to existing novel chapter files in Obsidian vault.
Also regenerates index files with proper Obsidian wikilinks.

Usage:
    python add_novel_nav.py [novels_dir]

    novels_dir: path to the Novels folder in Obsidian vault
                defaults to reading from config.local.toml (paths.obsidian_vault)
"""

import re
import sys
import tomllib
from pathlib import Path

NAV_MARKER = "<!-- nav-footer -->"


def get_wikilink_name(folder_name: str, chapter_num: int) -> str:
    return f"{chapter_num:04d}_-_{folder_name.replace(' ', '_')}"


def get_index_wikilink_name(folder_name: str) -> str:
    return f"{folder_name.replace(' ', '_')}_Index"


def get_chapter_filename(folder_name: str, chapter_num: int) -> str:
    return f"{chapter_num:04d} - {folder_name}.md"


def strip_nav_footer(content: str) -> str:
    pattern = re.compile(r'\n\n---\n\n' + re.escape(NAV_MARKER) + r'\n[^\n]*\n?$')
    return pattern.sub('', content).rstrip('\n')


def build_nav_footer(folder_name: str, chapter_nums: list[int], i: int) -> str:
    index_wikilink = get_index_wikilink_name(folder_name)

    if i > 0:
        prev_num = chapter_nums[i - 1]
        prev_link = f"[[{get_wikilink_name(folder_name, prev_num)}|← Ch {prev_num}]]"
    else:
        prev_link = "*(first)*"

    if i < len(chapter_nums) - 1:
        next_num = chapter_nums[i + 1]
        next_link = f"[[{get_wikilink_name(folder_name, next_num)}|Ch {next_num} →]]"
    else:
        next_link = "*(last)*"

    index_link = f"[[{index_wikilink}|Index]]"
    return f"\n\n---\n\n{NAV_MARKER}\n{prev_link} | {index_link} | {next_link}\n"


def create_index_file(novel_dir: Path, folder_name: str, chapter_nums: list[int]) -> Path:
    tag_slug = folder_name.lower().replace(' ', '-')
    index_filename = f"{folder_name.replace(' ', '_')}_Index.md"
    index_filepath = novel_dir / index_filename

    toc_lines = [
        f"- [[{get_wikilink_name(folder_name, num)}|Chapter {num}]]"
        for num in chapter_nums
    ]

    content = f"""---
tags:
  - book/novel
  - {tag_slug}
---

# {folder_name}

## Table of Contents

{chr(10).join(toc_lines)}
"""
    index_filepath.write_text(content, encoding='utf-8')
    return index_filepath


def process_novel(novel_dir: Path) -> int:
    folder_name = novel_dir.name

    chapter_files = sorted(novel_dir.glob("[0-9][0-9][0-9][0-9] - *.md"))
    if not chapter_files:
        return 0

    chapter_nums = []
    for f in chapter_files:
        m = re.match(r'^(\d{4}) - ', f.name)
        if m:
            chapter_nums.append(int(m.group(1)))
    chapter_nums.sort()

    print(f"  {folder_name}: {len(chapter_nums)} chapters ({chapter_nums[0]}–{chapter_nums[-1]})")

    create_index_file(novel_dir, folder_name, chapter_nums)

    for i, num in enumerate(chapter_nums):
        filepath = novel_dir / get_chapter_filename(folder_name, num)
        if not filepath.exists():
            continue
        content = filepath.read_text(encoding='utf-8')
        content = strip_nav_footer(content)
        content += build_nav_footer(folder_name, chapter_nums, i)
        filepath.write_text(content, encoding='utf-8')

    return len(chapter_nums)


def resolve_novels_path() -> Path:
    config_path = Path(__file__).parent / 'config.local.toml'
    if config_path.exists():
        with open(config_path, 'rb') as f:
            cfg = tomllib.load(f)
        vault = cfg.get('paths', {}).get('obsidian_vault', '')
        if vault:
            return Path(vault)
    # Fallback
    return Path('/Users/serpsaipong/Documents/00-inboxes/second-brain/03 - Resources/Books/Novels')


def main():
    if len(sys.argv) > 1:
        novels_path = Path(sys.argv[1])
    else:
        novels_path = resolve_novels_path()

    if not novels_path.exists():
        print(f"Error: Path does not exist: {novels_path}")
        sys.exit(1)

    print(f"Processing novels in: {novels_path}\n")

    total_novels = 0
    total_chapters = 0

    for novel_dir in sorted(novels_path.iterdir()):
        if not novel_dir.is_dir():
            continue
        count = process_novel(novel_dir)
        if count > 0:
            total_novels += 1
            total_chapters += count

    print(f"\nDone: {total_novels} novels, {total_chapters} chapters updated")


if __name__ == '__main__':
    main()
