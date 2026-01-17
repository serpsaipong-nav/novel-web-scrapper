#!/usr/bin/env python3
"""
Convert existing novel folders to Obsidian format
- Renames folders to Title Case
- Renames chapter files to: 0001 - Novel Name.md
- Adds YAML frontmatter with tags
- Creates index file with wikilinks
"""

import os
import re
import shutil


def sanitize_filename(name):
    """Convert name to valid filename"""
    clean = re.sub(r'[<>:"/\\|?*]', '', name)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean


def to_title_case(name):
    """Convert to Title Case for folder names"""
    # Handle underscores and spaces
    name = name.replace('_', ' ')
    return ' '.join(word.capitalize() for word in name.split())


def to_kebab_case(name):
    """Convert to kebab-case for tags"""
    clean = re.sub(r'[<>:"/\\|?*\']', '', name)
    clean = re.sub(r'\s+', '-', clean).strip().lower()
    return clean


def extract_chapter_number(filename):
    """Extract chapter number from various filename formats"""
    # Try format: Chapter_0001_...
    match = re.match(r'Chapter_(\d+)', filename)
    if match:
        return int(match.group(1))

    # Try format: Chapter 001 - ...
    match = re.match(r'Chapter\s+(\d+)\s*-', filename)
    if match:
        return int(match.group(1))

    # Try format: 0001 - ...
    match = re.match(r'(\d+)\s*-', filename)
    if match:
        return int(match.group(1))

    return None


def extract_content_from_old_format(filepath):
    """Extract content from old chapter file (after # heading)"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Remove existing YAML frontmatter if present
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            content = parts[2].strip()

    # Remove the first heading line
    lines = content.split('\n')
    content_lines = []
    found_heading = False

    for line in lines:
        if not found_heading and line.startswith('#'):
            found_heading = True
            continue
        content_lines.append(line)

    return '\n'.join(content_lines).strip()


def convert_novel_folder(source_dir, novel_name, output_base="novels"):
    """Convert a single novel folder to Obsidian format"""

    folder_name = to_title_case(sanitize_filename(novel_name))
    tag_slug = to_kebab_case(novel_name)

    target_dir = os.path.join(output_base, folder_name)

    print(f"\n{'='*60}")
    print(f"Converting: {novel_name}")
    print(f"Source: {source_dir}")
    print(f"Target: {target_dir}")
    print(f"{'='*60}")

    # Create target directory
    os.makedirs(target_dir, exist_ok=True)

    # Get list of chapter files
    chapter_files = []
    for f in os.listdir(source_dir):
        if f.endswith('.md') and not f.endswith('_Index.md') and not f.endswith('_Complete.md'):
            chapter_num = extract_chapter_number(f)
            if chapter_num is not None:
                chapter_files.append((chapter_num, f))

    chapter_files.sort(key=lambda x: x[0])

    print(f"Found {len(chapter_files)} chapters")

    converted = 0
    for chapter_num, old_filename in chapter_files:
        old_filepath = os.path.join(source_dir, old_filename)

        # Extract content
        content = extract_content_from_old_format(old_filepath)

        if not content or len(content) < 50:
            print(f"  Skipping chapter {chapter_num} (no content)")
            continue

        # Create new filename
        new_filename = f"{chapter_num:04d} - {folder_name}.md"
        new_filepath = os.path.join(target_dir, new_filename)

        # Create Obsidian format content
        obsidian_content = f"""---
tags:
  - book/novel
  - {tag_slug}
---

# {folder_name}

**Novel:** {folder_name}

**Chapter:** {chapter_num}

---

{content}
"""

        with open(new_filepath, 'w', encoding='utf-8') as f:
            f.write(obsidian_content)

        converted += 1
        if converted % 50 == 0:
            print(f"  Converted {converted} chapters...")

    print(f"  Converted {converted} chapters total")

    # Create index file
    create_index_file(target_dir, folder_name, tag_slug, [c[0] for c in chapter_files if c[0] <= converted or True])

    return converted


def create_index_file(novel_dir, folder_name, tag_slug, chapter_nums):
    """Create index file with links to all chapters"""

    # Get actual chapter files in directory
    actual_chapters = []
    for f in os.listdir(novel_dir):
        if f.endswith('.md') and f[0].isdigit():
            try:
                num = int(f.split(' - ')[0])
                actual_chapters.append(num)
            except (ValueError, IndexError):
                continue
    actual_chapters.sort()

    index_filename = f"{folder_name.replace(' ', '_')}_Index.md"
    index_filepath = os.path.join(novel_dir, index_filename)

    # Build Table of Contents
    toc_lines = []
    for num in actual_chapters:
        wikilink = f"{num:04d}_-_{folder_name.replace(' ', '_')}"
        toc_lines.append(f"- [Chapter {num}](#chapter-{num}) -> [[{wikilink}]]")

    toc_content = '\n'.join(toc_lines)

    index_content = f"""---
tags:
  - book/novel
  - {tag_slug}
---

# {folder_name}

## Table of Contents
---

{toc_content}
"""

    with open(index_filepath, 'w', encoding='utf-8') as f:
        f.write(index_content)

    print(f"  Created index file: {index_filename}")


def main():
    print("="*60)
    print("Novel Folder Converter to Obsidian Format")
    print("="*60)

    novels_to_convert = [
        ("novels/the_beginning_after_the_end", "The Beginning After The End"),
        ("novels/reincarnated_as_a_dragons_egg", "Reincarnated As A Dragons Egg"),
        ("novels/tate_no_yuusha_no_nariagari", "Tate No Yuusha No Nariagari"),
        ("novels/my_dragon_system", "My Dragon System"),
        ("novels/my_vampire_system", "My Vampire System"),
    ]

    for source_dir, novel_name in novels_to_convert:
        if os.path.exists(source_dir):
            convert_novel_folder(source_dir, novel_name, output_base="novels_obsidian")
        else:
            print(f"\nSkipping {novel_name} - folder not found: {source_dir}")

    print("\n" + "="*60)
    print("Conversion complete!")
    print("Output folder: novels_obsidian/")
    print("="*60)


if __name__ == "__main__":
    main()
