# Novel Web Scraper - Instructions for Claude

## Quick Reference

This project scrapes web novels and outputs Obsidian-compatible markdown files with YAML frontmatter and tags.

## How to Run

Always use `uv run` to execute the scraper:

```bash
uv run python scrape_novels.py --url "<URL>" --name "<Novel Name>" --start <N> --end <N>
```

## Supported Sites

### 1. lightnovelstranslations.com
```bash
uv run python scrape_novels.py \
  --url "https://lightnovelstranslations.com/novel/<novel-slug>/?tab=table_contents" \
  --name "Novel Name" \
  --start 1 --end 100
```
- Scraper class: `LightNovelTranslationsScraper`
- Fetches chapter list from table of contents page
- No Cloudflare issues

### 2. freewebnovel.com
```bash
uv run python scrape_novels.py \
  --url "https://freewebnovel.com/<novel-slug>.html" \
  --name "Novel Name" \
  --start 1 --end 100
```
- Scraper class: `FreeWebNovelScraper`
- Uses sequential chapter URLs: `/novel/<slug>/chapter-<N>`
- No Cloudflare issues

### 3. webnovel.com
```bash
uv run python scrape_novels.py \
  --url "https://www.webnovel.com/book/<book-id>" \
  --name "Novel Name" \
  --start 1 --end 100
```
- Scraper class: `WebNovelScraper`
- Extracts numeric book ID from URL
- Tries API first, falls back to page scraping
- May require Selenium for some content

### 4. novelbin.me / novelbin.com
```bash
# Uses NovelBinScraper class (not in CLI main() yet)
# Can use chapter list via AJAX or simple URL format
```
- Scraper class: `NovelBinScraper`
- Supports both simple URLs (`/b/<slug>/chapter-<N>`) and title-based URLs
- Has AJAX endpoint for chapter list

## CLI Arguments

| Argument | Short | Required | Default | Description |
|----------|-------|----------|---------|-------------|
| `--url` | `-u` | Yes | - | Novel URL from supported site |
| `--name` | `-n` | Yes | - | Novel name for folder and tags |
| `--start` | `-s` | No | 1 | Start chapter number |
| `--end` | `-e` | Yes | - | End chapter number |
| `--output` | `-o` | No | `novels_obsidian` | Output directory |
| `--delay` | `-d` | No | 1.5 | Delay between requests (seconds) |
| `--headless` | - | No | True | Run browser in headless mode (webnovel.com) |
| `--no-headless` | - | No | - | Show browser window |

## Output Format

```
<output>/
└── <Novel Name>/
    ├── 0001 - Novel Name.md
    ├── 0002 - Novel Name.md
    ├── ...
    └── Novel_Name_Index.md
```

Each chapter file has:
```yaml
---
tags:
  - book/novel
  - novel-name-kebab-case
---

# Novel Name

**Novel:** Novel Name
**Chapter:** 1

---

[Chapter content here]
```

## Scraper Architecture

```
NovelScraper (base class)
├── save_chapter() - saves markdown with frontmatter
├── create_index_file() - creates table of contents
├── sanitize_filename(), to_title_case(), to_kebab_case()
│
├── LightNovelTranslationsScraper
│   ├── get_chapter_list() - parses ToC page
│   └── scrape_chapter_by_url()
│
├── FreeWebNovelScraper
│   └── scrape_chapter() - sequential URLs
│
├── WebNovelScraper
│   ├── get_novel_info() - fetches chapter list
│   ├── scrape_chapter_api() - tries API first
│   └── scrape_chapter_by_url() - fallback
│
└── NovelBinScraper
    ├── get_chapter_list() - AJAX endpoint
    └── scrape_chapter_by_url()
```

## Adding New Sites

1. Create a new class inheriting from `NovelScraper`
2. Implement these methods:
   - `get_chapter_list(novel_slug)` → returns `[{'num': int, 'url': str, 'title': str}, ...]`
   - `scrape_chapter_by_url(url, retries=3)` → returns `(title, content)` or `(None, None)`
   - `scrape_range(novel_slug, novel_name, start_chapter, end_chapter, delay)`
3. Add URL detection in `main()` function with site-specific slug extraction

## Tips

- Run in background for large chapter counts: 624 chapters ≈ 15-20 min with 1.5s delay
- Check the novel's table of contents page to find total chapter count before scraping
- The delay helps avoid rate limiting; increase if getting blocked
- Failed chapters are listed at the end of the scrape
