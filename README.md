# Novel Web Scraper

A Python-based web scraper for downloading novels from various sources and converting them to Obsidian-compatible markdown format.

## Features

- Scrapes novels from **freewebnovel.com** (primary, no Cloudflare)
- Scrapes novels from **novelbin.com** (requires cloudscraper for Cloudflare bypass)
- Outputs **Obsidian-compatible** markdown with YAML frontmatter and tags
- Creates **index files** with wikilinks to all chapters
- **CLI interface** with URL parameters
- Automatic retry on failures
- Progress tracking and error reporting

## Requirements

- Python 3.13+
- [uv](https://github.com/astral-sh/uv) package manager

## Installation

```bash
# Clone or navigate to the project directory
cd novel-web-scrapper

# Install dependencies using uv
uv sync
```

## Quick Start

### Command Line Interface (Recommended)

```bash
# Basic usage
uv run python scrape_novels.py --url URL --name "Novel Name" --end CHAPTERS

# Example: Scrape a novel from freewebnovel.com
uv run python scrape_novels.py \
  --url "https://freewebnovel.com/my-vampire-system.html" \
  --name "My Vampire System" \
  --end 2545

# With all options
uv run python scrape_novels.py \
  --url "https://freewebnovel.com/rezero-kara-hajimeru-isekai-seikatsu-wn.html" \
  --name "Re Zero WN" \
  --start 1 \
  --end 549 \
  --output novels_obsidian \
  --delay 1.5
```

### CLI Options

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--url` | `-u` | Yes | - | Novel URL from freewebnovel.com |
| `--name` | `-n` | Yes | - | Novel name (used for folder and tags) |
| `--end` | `-e` | Yes | - | Last chapter number to scrape |
| `--start` | `-s` | No | 1 | First chapter number to scrape |
| `--output` | `-o` | No | novels_obsidian | Output directory |
| `--delay` | `-d` | No | 1.5 | Delay between requests (seconds) |

### Python API

```python
from scrape_novels import FreeWebNovelScraper

scraper = FreeWebNovelScraper(output_dir="novels_obsidian")
scraper.scrape_range(
    novel_slug="my-vampire-system",
    novel_name="My Vampire System",
    start_chapter=1,
    end_chapter=2545,
    delay=1.5
)
```

### For novelbin.com (Cloudflare protected)

```python
from scrape_novels import NovelBinScraper

scraper = NovelBinScraper(output_dir="novels_obsidian")

# Simple URL format (/chapter-1, /chapter-2)
scraper.scrape_range(
    novel_slug="my-werewolf-system-novel",
    novel_name="My Werewolf System",
    start_chapter=1,
    end_chapter=325,
    delay=2
)

# Title-based URLs (/chapter-1-the-beginning)
scraper.scrape_with_chapter_list(
    novel_slug="shadow-slave",
    novel_name="Shadow Slave",
    start_chapter=1,
    delay=2
)
```

## Supported Sites

| Site | Cloudflare | Scraper Class | Notes |
|------|------------|---------------|-------|
| freewebnovel.com | No | `FreeWebNovelScraper` | Recommended, faster |
| novelbin.com | Yes | `NovelBinScraper` | May get blocked |

## Output Structure

```
novels_obsidian/
├── My Vampire System/
│   ├── 0001 - My Vampire System.md
│   ├── 0002 - My Vampire System.md
│   ├── ...
│   └── My_Vampire_System_Index.md
├── Re Zero WN/
│   ├── 0001 - Re Zero Wn.md
│   ├── ...
│   └── Re_Zero_Wn_Index.md
└── ...
```

## Chapter File Format

Each chapter file follows the Obsidian format:

```markdown
---
tags:
  - book/novel
  - my-vampire-system
---

# My Vampire System

**Novel:** My Vampire System

**Chapter:** 1

---

[Chapter content here...]
```

## Index File Format

The index file contains a Table of Contents with Obsidian wikilinks:

```markdown
---
tags:
  - book/novel
  - my-vampire-system
---

# My Vampire System

## Table of Contents
---

- [Chapter 1](#chapter-1) -> [[0001_-_My_Vampire_System]]
- [Chapter 2](#chapter-2) -> [[0002_-_My_Vampire_System]]
...
```

## Finding Novel URLs

### freewebnovel.com
1. Search for the novel on the site
2. Copy the URL: `https://freewebnovel.com/novel-name.html`
3. Check the last chapter number on the novel page

### novelbin.com
1. Navigate to the novel page
2. Copy the slug from URL: `novelbin.com/b/novel-slug`

## Files

| File | Description |
|------|-------------|
| `scrape_novels.py` | Main scraper with CLI and scraper classes |
| `main.py` | Interactive scraper for freewebnovel.com with offset detection |
| `convert_to_obsidian.py` | Convert existing folders to Obsidian format |
| `test_scrape.py` | Test script for verifying scraper works |

## Troubleshooting

### 403 Forbidden Errors
- **freewebnovel.com**: Usually works without issues
- **novelbin.com**: Uses Cloudflare protection, may get blocked
  - Try increasing delay between requests
  - Site may have updated protection

### Empty Content
- The chapter may not exist on the site
- Check if you're using the correct URL format
- Some chapters may be premium/locked

### Missing Chapters
- Some novels have gaps in chapter numbers
- The site may not have all chapters available

## License

For personal use only. Respect the original content creators and website terms of service.
