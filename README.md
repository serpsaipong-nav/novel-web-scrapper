# Novel Web Scraper

A Python-based web scraper for downloading novels from various sources and converting them to Obsidian-compatible markdown format.

## Features

- Scrapes novels from **novelbin.com** (supports both simple and title-based URLs)
- Outputs **Obsidian-compatible** markdown with YAML frontmatter and tags
- Creates **index files** with wikilinks to all chapters
- Handles anti-bot protection using `cloudscraper`
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

### Method 1: Simple URL Format (numbered chapters)

For novels with simple chapter URLs like `/chapter-1`, `/chapter-2`:

```python
from scrape_novels import NovelBinScraper

scraper = NovelBinScraper(output_dir="novels_obsidian")
scraper.scrape_range(
    novel_slug="my-werewolf-system-novel",
    novel_name="My Werewolf System",
    start_chapter=1,
    end_chapter=325,
    delay=2  # seconds between requests
)
```

### Method 2: Title-Based URLs (chapter list)

For novels with title-based URLs like `/chapter-1-the-beginning`:

```python
from scrape_novels import NovelBinScraper

scraper = NovelBinScraper(output_dir="novels_obsidian")
scraper.scrape_with_chapter_list(
    novel_slug="shadow-slave",
    novel_name="Shadow Slave",
    start_chapter=1,
    end_chapter=None,  # None = all available
    delay=2
)
```

### Running from Command Line

```bash
# Run the main script
uv run python scrape_novels.py

# Or run a custom script
uv run python -c "
from scrape_novels import NovelBinScraper
scraper = NovelBinScraper(output_dir='novels_obsidian')
scraper.scrape_range('novel-slug', 'Novel Name', 1, 100, delay=2)
"
```

## How It Works

### Flowchart

```
┌─────────────────────────────────────────────────────────────────┐
│                        START                                     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  1. INITIALIZE SCRAPER                                          │
│     - Create cloudscraper session                               │
│     - Set browser headers                                       │
│     - Configure output directory                                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  2. DETERMINE URL TYPE                                          │
│     ┌──────────────────┬──────────────────┐                     │
│     │  Simple URLs?    │  Title-based?    │                     │
│     │  /chapter-1      │  /chapter-1-xxx  │                     │
│     └────────┬─────────┴────────┬─────────┘                     │
│              │                  │                               │
│              ▼                  ▼                               │
│     scrape_range()    scrape_with_chapter_list()               │
└─────────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┴───────────────────┐
          ▼                                       ▼
┌─────────────────────┐             ┌─────────────────────────────┐
│ Simple URL Mode     │             │ Chapter List Mode           │
│                     │             │                             │
│ Generate URL:       │             │ 1. Fetch AJAX chapter list  │
│ /b/{slug}/chapter-N │             │ 2. Parse all chapter URLs   │
│                     │             │ 3. Extract chapter numbers  │
└─────────┬───────────┘             └──────────────┬──────────────┘
          │                                        │
          └────────────────┬───────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  3. FOR EACH CHAPTER                                            │
│     ┌─────────────────────────────────────────────────────┐     │
│     │  a. Request chapter URL                             │     │
│     │  b. Parse HTML with BeautifulSoup                   │     │
│     │  c. Extract title from selectors                    │     │
│     │  d. Extract content from #chr-content               │     │
│     │  e. Filter out navigation/ad text                   │     │
│     │  f. Retry up to 3 times on failure                  │     │
│     └─────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  4. SAVE CHAPTER (Obsidian Format)                              │
│     ┌─────────────────────────────────────────────────────┐     │
│     │  Filename: 0001 - Novel Name.md                     │     │
│     │                                                     │     │
│     │  Content:                                           │     │
│     │  ---                                                │     │
│     │  tags:                                              │     │
│     │    - book/novel                                     │     │
│     │    - novel-name-slug                                │     │
│     │  ---                                                │     │
│     │  # Novel Name                                       │     │
│     │  **Novel:** Novel Name                              │     │
│     │  **Chapter:** 1                                     │     │
│     │  ---                                                │     │
│     │  [chapter content]                                  │     │
│     └─────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  5. DELAY (default 2 seconds)                                   │
│     - Respect server rate limits                                │
│     - Avoid getting blocked                                     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │ More chapters?  │
                    └────────┬────────┘
                             │
              ┌──────────────┴──────────────┐
              │ YES                         │ NO
              ▼                             ▼
     [Go back to step 3]    ┌─────────────────────────────────────┐
                            │  6. CREATE INDEX FILE               │
                            │     - Generate Table of Contents    │
                            │     - Add Obsidian wikilinks        │
                            │     - Save as Novel_Name_Index.md   │
                            └─────────────────────────────────────┘
                                            │
                                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  7. PRINT SUMMARY                                               │
│     - Successful chapters count                                 │
│     - Failed chapters list                                      │
│     - Output folder location                                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                          END                                     │
└─────────────────────────────────────────────────────────────────┘
```

## Output Structure

```
novels_obsidian/
├── My Werewolf System/
│   ├── 0001 - My Werewolf System.md
│   ├── 0002 - My Werewolf System.md
│   ├── ...
│   └── My_Werewolf_System_Index.md
├── Shadow Slave/
│   ├── 0001 - Shadow Slave.md
│   ├── ...
│   └── Shadow_Slave_Index.md
└── ...
```

## Chapter File Format

Each chapter file follows the Obsidian format:

```markdown
---
tags:
  - book/novel
  - my-werewolf-system
---

# My Werewolf System

**Novel:** My Werewolf System

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
  - my-werewolf-system
---

# My Werewolf System

## Table of Contents
---

- [Chapter 1](#chapter-1) -> [[0001_-_My_Werewolf_System]]
- [Chapter 2](#chapter-2) -> [[0002_-_My_Werewolf_System]]
- [Chapter 3](#chapter-3) -> [[0003_-_My_Werewolf_System]]
...
```

## Finding Novel Slugs

The novel slug is the URL path identifier. For example:

| URL | Slug |
|-----|------|
| `novelbin.com/b/my-werewolf-system-novel` | `my-werewolf-system-novel` |
| `novelbin.com/b/shadow-slave` | `shadow-slave` |
| `novelbin.com/b/the-beginning-after-the-end` | `the-beginning-after-the-end` |

## Determining URL Type

1. **Check a chapter URL manually:**
   - Simple: `novelbin.com/b/novel-slug/chapter-1`
   - Title-based: `novelbin.com/b/novel-slug/chapter-1-the-beginning`

2. **Test with the scraper:**
   ```python
   # If this returns content, use scrape_range()
   scraper.scrape_chapter('novel-slug', 1)

   # If empty, use scrape_with_chapter_list()
   scraper.scrape_with_chapter_list('novel-slug', 'Novel Name', 1)
   ```

## Converting Existing Novels

To convert existing novel folders to Obsidian format:

```bash
uv run python convert_to_obsidian.py
```

## Troubleshooting

### 403 Forbidden Errors
The scraper uses `cloudscraper` to bypass anti-bot protection. If you still get 403 errors:
- Increase delay between requests
- The site may have changed its protection

### Empty Content
Some chapters may return empty content if:
- The chapter doesn't exist on the site
- The site returns a placeholder page
- Check if the novel uses title-based URLs

### Missing Chapters
- Some novels have gaps in chapter numbers
- Use `scrape_with_chapter_list()` to get only available chapters

## Files

| File | Description |
|------|-------------|
| `scrape_novels.py` | Main scraper with `NovelBinScraper` class |
| `convert_to_obsidian.py` | Convert existing folders to Obsidian format |
| `test_scrape.py` | Test script for verifying scraper works |

## License

For personal use only. Respect the original content creators and website terms of service.
