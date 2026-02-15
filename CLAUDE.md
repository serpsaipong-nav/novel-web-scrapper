# Web Scraper Suite - Instructions for Claude

## Quick Reference

This project contains three scrapers that track content with DuckDB and sync to Obsidian:

- **`scrape_novels.py`** - Scrapes web novels from multiple sites
- **`scrape_blogs.py`** - Scrapes all Databricks blog posts
- **`scrape_medium.py`** - Scrapes Medium blog posts by username

## How to Run

Always use `uv run`:

```bash
uv run python scrape_novels.py <command> [options]
```

## Commands

### Novel Management

```bash
# Add a novel to track
uv run python scrape_novels.py add --url "<URL>" --name "Novel Name"

# List all tracked novels
uv run python scrape_novels.py list
uv run python scrape_novels.py list --json

# Remove a novel
uv run python scrape_novels.py remove "Novel Name"
```

### Check & Sync

```bash
# Check for new chapters (all novels)
uv run python scrape_novels.py check
uv run python scrape_novels.py check --json

# Check specific novel
uv run python scrape_novels.py check --name "Novel Name"

# Download new chapters
uv run python scrape_novels.py sync --all
uv run python scrape_novels.py sync --name "Novel Name"

# Download sequentially (not parallel)
uv run python scrape_novels.py sync --all --sequential
```

### Obsidian Integration

```bash
# Move chapters to Obsidian vault
uv run python scrape_novels.py move --all
uv run python scrape_novels.py move --name "Novel Name"

# Import existing novels from Obsidian folder
uv run python scrape_novels.py scan-obsidian
```

### Configuration

```bash
# View current config
uv run python scrape_novels.py config show

# Set Obsidian vault path
uv run python scrape_novels.py config set obsidian_vault "/path/to/vault/Novels"

# Set other options
uv run python scrape_novels.py config set scraper.max_workers 6
uv run python scrape_novels.py config set scraper.delay 2.0
```

### Legacy One-Time Scrape

```bash
# Scrape without tracking (backwards compatible)
uv run python scrape_novels.py scrape \
  --url "https://lightnovelstranslations.com/novel/novel-slug/" \
  --name "Novel Name" \
  --start 1 --end 100 \
  --delay 1.5
```

## Supported Sites

| Site | URL Pattern |
|------|-------------|
| lightnovelstranslations.com | `https://lightnovelstranslations.com/novel/<slug>/` |
| freewebnovel.com | `https://freewebnovel.com/<slug>.html` |
| novelbin.com | `https://novelbin.com/b/<slug>/` |

## Configuration Files

- `config.toml` - Default settings (committed to git)
- `config.local.toml` - Machine-specific overrides (gitignored)
- `novels.db` - DuckDB database (gitignored)

### Key Config Options

```toml
[paths]
staging_dir = "novels_obsidian"      # Download folder
obsidian_vault = "/path/to/vault"    # Obsidian Novels folder
database = "novels.db"                # DuckDB file

[scraper]
delay = 1.5                          # Delay between requests
max_workers = 4                      # Parallel download workers
max_retries = 3                      # Retry attempts
parallel_delay_multiplier = 2.0      # Delay multiplier for parallel
```

## Output Format

```
novels_obsidian/
└── Novel Name/
    ├── 0001 - Novel Name.md
    ├── 0002 - Novel Name.md
    └── Novel_Name_Index.md
```

Each chapter has YAML frontmatter:
```yaml
---
tags:
  - book/novel
  - novel-name
---
```

## Database Schema

```sql
novels (id, name, slug, url, site, status, total_chapters, ...)
chapters (id, novel_id, chapter_num, title, file_path, in_obsidian, ...)
sync_logs (id, novel_id, checked_at, latest_available, new_chapters_found, ...)
```

## n8n Integration

Import `n8n/novel_sync_workflow.json` into n8n:

1. Open n8n → Import Workflow
2. Configure the "Set Script Path" node with your script path
3. Adjust schedule in "Schedule Trigger" node
4. Optional: Add notification node (Discord, Telegram, etc.)

## Typical Workflow

```bash
# Initial setup
uv run python scrape_novels.py config set obsidian_vault "~/Obsidian/Vault/Novels"

# Add novels to track
uv run python scrape_novels.py add --url "..." --name "Novel A"
uv run python scrape_novels.py add --url "..." --name "Novel B"

# Daily: check and sync
uv run python scrape_novels.py check
uv run python scrape_novels.py sync --all
uv run python scrape_novels.py move --all
```

## Adding New Sites

1. Create a new class inheriting from `NovelScraper`
2. Set `SITE` and `BASE_URL` class attributes
3. Implement:
   - `get_chapter_list(slug)` → returns `[{'num', 'url', 'title'}, ...]`
   - `scrape_chapter_by_url(url)` → returns `(title, content)`
   - `get_novel_status(slug)` → returns `'ongoing'`, `'completed'`, or `'hiatus'`
4. Add to `SCRAPERS` dict
5. Update `extract_slug_from_url()` function

---

# Databricks Blog Scraper (`scrape_blogs.py`)

## Overview

Scrapes all Databricks blog posts (~2,600+) and saves them as Obsidian markdown with tags `clipping`, `databricks`. Uses Gatsby page-data JSON endpoints (no browser required).

## How It Works

Databricks blog is a Gatsby static site backed by Drupal CMS. Each blog post has a predictable JSON endpoint containing the full article data:

- **New posts**: `/en-blog-assets/page-data/blog/{slug}/page-data.json`
- **Legacy posts**: `/blog-legacy-assets/page-data/blog/{YYYY/MM/DD/slug.html}/page-data.json`

The scraper fetches these JSON endpoints directly with `requests` - no Selenium, no browser, no JS rendering needed.

## How to Run

```bash
uv run python scrape_blogs.py <command> [options]
```

## Commands

### Discovery

```bash
# Fetch sitemap and add new blog post URLs to database
uv run python scrape_blogs.py discover
```

This walks the Databricks sitemap hierarchy:
1. `sitemap-index.xml` (top-level)
2. `en-blog-assets/sitemap/sitemap-index.xml` + `blog-legacy-assets/sitemap/sitemap-index.xml`
3. Individual `sitemap-0.xml` files containing blog URLs

### Scraping

```bash
# Download all pending posts (sequential, default)
uv run python scrape_blogs.py scrape

# Download in parallel (4 workers by default)
uv run python scrape_blogs.py scrape --parallel

# Download in parallel with custom worker count
uv run python scrape_blogs.py scrape --workers 8

# Force sequential mode
uv run python scrape_blogs.py scrape --sequential

# Download a single post by slug
uv run python scrape_blogs.py scrape --slug "delta-lake-explained"

# Download first 50 pending posts
uv run python scrape_blogs.py scrape --limit 50

# Combine flags
uv run python scrape_blogs.py scrape --parallel --limit 100
```

### Listing & Status

```bash
# List all posts
uv run python scrape_blogs.py list

# Filter by status
uv run python scrape_blogs.py list --status pending
uv run python scrape_blogs.py list --status downloaded
uv run python scrape_blogs.py list --status failed

# JSON output
uv run python scrape_blogs.py list --json

# Summary statistics
uv run python scrape_blogs.py status
```

### Obsidian Integration

```bash
# Move downloaded posts to Obsidian vault
uv run python scrape_blogs.py move --all
```

### Error Recovery

```bash
# Reset failed posts back to pending for retry
uv run python scrape_blogs.py retry
```

### Configuration

```bash
# View current config
uv run python scrape_blogs.py config show

# Set Obsidian vault path
uv run python scrape_blogs.py config set obsidian_vault "/path/to/vault/Blogs"

# Set delay between requests
uv run python scrape_blogs.py config set delay 2.0
```

## Configuration

Settings live in `config.toml` under the `[blogs]` section:

```toml
[blogs]
staging_dir = "blogs_obsidian"    # Download folder
obsidian_vault = ""               # Obsidian vault path (set in config.local.toml)
database = "blogs.db"             # DuckDB file (separate from novels.db)
delay = 1.0                       # Delay between requests (seconds)
max_retries = 3                   # Retry attempts per post
max_workers = 4                   # Parallel workers (used with --parallel)
```

Machine-specific overrides go in `config.local.toml` (gitignored).

## Output Format

Files are named by title (not slug):

```
blogs_obsidian/
└── Databricks/
    ├── Delta Lake Explained.md
    ├── Databricks Lakebase is now Generally Available.md
    └── Introducing Apache Spark 2.4.md
```

Each post uses the Obsidian clipping format:

```yaml
---
title: "Blog Post Title"
source: "https://www.databricks.com/blog/..."
author:
  - "[[Author Name]]"
  - "[[Co-Author Name]]"
published: 2024-11-15
created: 2026-02-05
description: "Meta description from the page"
tags:
  - "clippings"
  - "databricks"
---
#### Summary

- AI-generated bullet point summary (when available)

{article content in markdown}
```

## Database Schema (`blogs.db`)

```sql
blog_posts (id, slug, url, title, author, publish_date, categories,
            word_count, char_count, file_path, status, downloaded_at,
            in_obsidian, moved_at, created_at)

blog_sync_logs (id, synced_at, total_in_sitemap, new_posts_found,
                posts_downloaded, posts_failed, status)
```

Post statuses: `pending` -> `downloaded` or `failed`

## URL Patterns & Slugs

| Type | Blog URL | Database Slug |
|------|----------|---------------|
| New | `https://www.databricks.com/blog/some-slug` | `some-slug` |
| Legacy | `https://www.databricks.com/blog/2020/09/15/some-slug.html` | `2020-09-15-some-slug` |

## Architecture (4 classes)

```
Config               - Reads [blogs] section from config.toml
BlogDatabase         - DuckDB wrapper (blog_posts + blog_sync_logs)
DatabricksBlogScraper - Sitemap parsing, page-data JSON fetching, HTML-to-markdown
BlogManager          - Orchestrator (discover, scrape, move, etc.)
```

## Typical Workflow

```bash
# Initial setup
uv run python scrape_blogs.py config set obsidian_vault "~/Obsidian/Vault/Blogs"

# Discover all blog URLs from sitemap
uv run python scrape_blogs.py discover

# Download all posts (sequential by default, or use --parallel)
uv run python scrape_blogs.py scrape --parallel

# Check progress
uv run python scrape_blogs.py status

# Move to Obsidian
uv run python scrape_blogs.py move --all

# Later: discover new posts and download them
uv run python scrape_blogs.py discover
uv run python scrape_blogs.py scrape
```

## Error Handling

- Per-post retry with exponential backoff (1s, 2s, 4s)
- Graceful Ctrl+C shutdown (preserves DB state, can resume later)
- Posts with < 100 chars content are marked as `failed`
- Use `retry` command to reset failed posts for re-download
- Re-running `scrape` only processes `pending` posts (already downloaded ones are skipped)

---

# Medium Blog Scraper (`scrape_medium.py`)

## Overview

Scrapes Medium blog posts by username and saves them as Obsidian markdown with tags `clippings`, `medium`. Uses the Medium RSS feed at `https://medium.com/feed/@username` which returns full HTML content in `<content:encoded>` — no browser/Selenium needed.

## How It Works

Medium provides an RSS feed for each user. The `<content:encoded>` element contains the full article HTML. For small accounts (~10-11 posts), the RSS feed covers all posts. The scraper:

1. **Discover**: Fetches RSS XML, parses all `<item>` elements, stores metadata + full HTML in DuckDB
2. **Scrape**: Converts stored HTML to markdown files (no network needed — content is already in DB)

## How to Run

```bash
uv run python scrape_medium.py <command> [options]
```

## Commands

### User Management

```bash
# Add a Medium username to track
uv run python scrape_medium.py add-user vutrinh274

# Remove a tracked username
uv run python scrape_medium.py remove-user vutrinh274
```

### Discovery

```bash
# Fetch RSS feed and add new posts to database
uv run python scrape_medium.py discover

# Discover for a specific user only
uv run python scrape_medium.py discover --user vutrinh274
```

### Scraping

```bash
# Convert all pending posts to markdown (sequential, default)
uv run python scrape_medium.py scrape

# Convert in parallel (4 workers by default)
uv run python scrape_medium.py scrape --parallel

# Convert with custom worker count
uv run python scrape_medium.py scrape --workers 8

# Convert a single post by slug
uv run python scrape_medium.py scrape --slug "some-post-slug-abc123"

# Convert first N pending posts
uv run python scrape_medium.py scrape --limit 5
```

### Listing & Status

```bash
# List all posts
uv run python scrape_medium.py list

# Filter by status
uv run python scrape_medium.py list --status pending
uv run python scrape_medium.py list --status downloaded
uv run python scrape_medium.py list --status failed

# Filter by user
uv run python scrape_medium.py list --user vutrinh274

# JSON output
uv run python scrape_medium.py list --json

# Summary statistics
uv run python scrape_medium.py status
uv run python scrape_medium.py status --user vutrinh274
```

### Obsidian Integration

```bash
# Move downloaded posts to Obsidian vault
uv run python scrape_medium.py move --all
```

### Error Recovery

```bash
# Reset failed posts back to pending for retry
uv run python scrape_medium.py retry
```

### Configuration

```bash
# View current config
uv run python scrape_medium.py config show

# Set Obsidian vault path
uv run python scrape_medium.py config set obsidian_vault "/path/to/vault/Blogs"

# Set delay between requests
uv run python scrape_medium.py config set delay 2.0
```

## Configuration

Settings live in `config.toml` under the `[medium]` section:

```toml
[medium]
staging_dir = "medium_obsidian"   # Download folder
obsidian_vault = ""               # Obsidian vault path (set in config.local.toml)
database = "medium.db"            # DuckDB file (separate from novels.db/blogs.db)
delay = 1.0                       # Delay between requests (seconds)
max_retries = 3                   # Retry attempts per post
max_workers = 4                   # Parallel workers (used with --parallel)
users = []                        # Tracked Medium usernames (without @)
```

Machine-specific overrides go in `config.local.toml` (gitignored).

### Authentication (for member-only posts)

To scrape full member-only content, configure your Medium session cookies in `config.local.toml` (gitignored):

```bash
uv run python scrape_medium.py config set sid "YOUR_SID_COOKIE"
uv run python scrape_medium.py config set uid "YOUR_UID_COOKIE"
```

To get these cookies:
1. Open Chrome, go to `medium.com` (logged in)
2. F12 → Application → Cookies → `https://medium.com`
3. Copy the `sid` and `uid` cookie values

Without auth, member-only posts will only have preview content. With auth, the scraper uses Medium's GraphQL API to fetch full articles.

## Output Format

Files are named by title (not slug):

```
medium_obsidian/
└── Medium/
    ├── I spent 8 hours understanding Apache Sparks memory management.md
    ├── Why do we need open table formats like Delta Lake or Iceberg.md
    └── ...
```

Each post uses the Obsidian clipping format:

```yaml
---
title: "Post Title"
source: "https://blog.dataengineerthings.org/post-slug-abc123"
author:
  - "[[Vu Trinh]]"
published: 2025-02-01
created: 2026-02-06
description: "Short description"
tags:
  - "clippings"
  - "medium"
---
```

## Database Schema (`medium.db`)

```sql
medium_posts (id, slug, username, url, title, author, description,
              publish_date, updated_date, categories, content_html,
              word_count, char_count, file_path, status, downloaded_at,
              in_obsidian, moved_at, created_at)

medium_sync_logs (id, synced_at, username, total_in_feed, new_posts_found,
                  posts_downloaded, posts_failed, status)
```

Post statuses: `pending` -> `downloaded` or `failed`

## Architecture (4 classes)

```
Config          - Reads [medium] section from config.toml
MediumDatabase  - DuckDB wrapper (medium_posts + medium_sync_logs)
MediumScraper   - RSS feed parsing, HTML-to-markdown conversion
MediumManager   - Orchestrator (add-user, discover, scrape, move, etc.)
```

## Typical Workflow

```bash
# Initial setup
uv run python scrape_medium.py config set obsidian_vault "~/Obsidian/Vault/Blogs"

# Add a Medium user
uv run python scrape_medium.py add-user vutrinh274

# Discover posts from RSS feed
uv run python scrape_medium.py discover

# Convert to markdown
uv run python scrape_medium.py scrape

# Check progress
uv run python scrape_medium.py status

# Move to Obsidian
uv run python scrape_medium.py move --all

# Later: discover new posts
uv run python scrape_medium.py discover
uv run python scrape_medium.py scrape
```

## Error Handling

- Per-post retry with content validation (< 100 chars = failed)
- Graceful Ctrl+C shutdown (preserves DB state, can resume later)
- Use `retry` command to reset failed posts for re-download
- Re-running `scrape` only processes `pending` posts (already converted ones are skipped)

---

# Raindrop.io Bookmark Scraper (`scrape_raindrop.py`)

## Overview

Scrapes web pages bookmarked in Raindrop.io and saves them as Obsidian markdown with tags `clippings`, `raindrop` (plus user's Raindrop tags). Uses the Raindrop.io REST API to fetch bookmarks, then scrapes the actual web pages for content. Medium URLs are optionally routed to the Medium scraper instead of being scraped directly.

## How It Works

1. **Discover**: Fetches all bookmarks from Raindrop.io API (`GET /rest/v1/raindrops/0`), paginates automatically
2. **Medium Routing**: Medium URLs detected during discover are marked `skipped_medium` and inserted into `medium.db` as pending — picked up by the next Medium scrape run
3. **Scrape**: Fetches HTML from each bookmarked URL, extracts article/main content, converts to markdown

## How to Run

```bash
uv run python scrape_raindrop.py <command> [options]
```

## Commands

### Discovery

```bash
# Fetch bookmarks from Raindrop.io API
uv run python scrape_raindrop.py discover

# Discover without routing Medium URLs
uv run python scrape_raindrop.py discover --no-route-medium
```

### Scraping

```bash
# Download all pending bookmarks (sequential, default)
uv run python scrape_raindrop.py scrape

# Download in parallel (4 workers by default)
uv run python scrape_raindrop.py scrape --parallel

# Download with custom worker count
uv run python scrape_raindrop.py scrape --workers 8

# Download a single bookmark by Raindrop ID
uv run python scrape_raindrop.py scrape --id 123456

# Download first N pending bookmarks
uv run python scrape_raindrop.py scrape --limit 10
```

### Listing & Status

```bash
# List all bookmarks
uv run python scrape_raindrop.py list

# Filter by status
uv run python scrape_raindrop.py list --status pending
uv run python scrape_raindrop.py list --status downloaded
uv run python scrape_raindrop.py list --status failed
uv run python scrape_raindrop.py list --status skipped_medium

# JSON output
uv run python scrape_raindrop.py list --json

# Summary statistics
uv run python scrape_raindrop.py status
```

### Obsidian Integration

```bash
# Move downloaded bookmarks to Obsidian vault
uv run python scrape_raindrop.py move --all
```

### Error Recovery

```bash
# Reset failed bookmarks back to pending for retry
uv run python scrape_raindrop.py retry
```

### Failure Diagnosis (`fix`)

```bash
# Show detailed failure report (for LLM consumption / debugging)
uv run python scrape_raindrop.py fix

# JSON output (for piping to LLM tools)
uv run python scrape_raindrop.py fix --json

# Show details for a single bookmark
uv run python scrape_raindrop.py fix --id 123456

# Limit output
uv run python scrape_raindrop.py fix --limit 5

# Auto-fix via Ollama (requires ollama_url config — not yet implemented)
uv run python scrape_raindrop.py fix --auto
```

The `fix` command outputs error reason, detail, and an HTML preview for each failed bookmark. Error categories: `http_XXX` (HTTP errors), `timeout`, `no_content`, `parse_error`, `connection_error`.

### Configuration

```bash
# View current config
uv run python scrape_raindrop.py config show

# Set Raindrop API token
uv run python scrape_raindrop.py config set test_token "YOUR_TOKEN"

# Set Obsidian vault path
uv run python scrape_raindrop.py config set obsidian_vault "/path/to/vault/Bookmarks"

# Set delay between requests
uv run python scrape_raindrop.py config set delay 2.0

# Disable Medium routing
uv run python scrape_raindrop.py config set route_medium false
```

## Configuration

Settings live in `config.toml` under the `[raindrop]` section:

```toml
[raindrop]
test_token = ""                   # API token (set in config.local.toml)
staging_dir = "raindrop_obsidian" # Download folder
obsidian_vault = ""               # Obsidian vault path (set in config.local.toml)
database = "raindrop.db"          # DuckDB file
delay = 1.0                       # Delay between requests (seconds)
max_retries = 3                   # Retry attempts per bookmark
max_workers = 4                   # Parallel workers (used with --parallel)
route_medium = true               # Route Medium URLs to Medium scraper
medium_domains = []               # Extra Medium publication custom domains
```

Machine-specific overrides go in `config.local.toml` (gitignored).

### Getting a Raindrop API Token

1. Go to https://app.raindrop.io/settings/integrations
2. Click "Create new app"
3. Copy the "Test token"
4. `uv run python scrape_raindrop.py config set test_token "YOUR_TOKEN"`

## Medium URL Routing

When `route_medium = true` (default), Medium URLs are detected during `discover` and routed to the Medium scraper:

- URLs on `medium.com`, `*.medium.com`, and known Medium publication domains are detected
- Routed bookmarks are marked `skipped_medium` in `raindrop.db`
- A pending entry is inserted directly into `medium.db`
- The next `scrape_medium.py scrape` run processes these routed posts
- All four pipelines run in parallel in n8n, so routed posts are picked up the same day

Built-in Medium domain detection includes: `towardsdatascience.com`, `betterprogramming.pub`, `levelup.gitconnected.com`, `javascript.plainenglish.io`, `blog.devgenius.io`, `itnext.io`, and more. Add custom domains via `medium_domains` config.

## Output Format

Files are named by title (not URL):

```
raindrop_obsidian/
└── Raindrop/
    ├── Some Interesting Article.md
    ├── How to Build a Data Pipeline.md
    └── ...
```

Each bookmark uses the Obsidian clipping format with merged tags:

```yaml
---
title: "Article Title"
source: "https://example.com/article"
author:
  - "[[Author Name]]"
published: 2025-03-15
created: 2026-02-15
description: "Meta description from page"
tags:
  - "clippings"
  - "raindrop"
  - "user-tag-from-raindrop"
  - "another-tag"
---
```

## Database Schema (`raindrop.db`)

```sql
raindrop_bookmarks (id, raindrop_id, url, title, domain, excerpt, note,
                    author, tags, bookmark_type, raindrop_created,
                    raindrop_updated, cover_url, word_count, char_count,
                    file_path, status, routed_to, downloaded_at,
                    in_obsidian, moved_at, created_at)

raindrop_sync_logs (id, synced_at, total_in_api, new_bookmarks_found,
                    bookmarks_downloaded, bookmarks_failed,
                    bookmarks_routed_medium, status)
```

Bookmark statuses: `pending` -> `downloaded` or `failed` or `skipped_medium`

## Architecture (4 classes)

```
Config            - Reads [raindrop] section from config.toml
RaindropDatabase  - DuckDB wrapper (raindrop_bookmarks + raindrop_sync_logs)
RaindropScraper   - Raindrop API, Medium detection, HTML-to-markdown
RaindropManager   - Orchestrator (discover, scrape, move, etc.)
```

## Typical Workflow

```bash
# Initial setup
uv run python scrape_raindrop.py config set test_token "YOUR_TOKEN"
uv run python scrape_raindrop.py config set obsidian_vault "~/Obsidian/Vault/Bookmarks"

# Discover bookmarks from Raindrop.io
uv run python scrape_raindrop.py discover

# Download pages as markdown
uv run python scrape_raindrop.py scrape --parallel

# Check progress
uv run python scrape_raindrop.py status

# Move to Obsidian
uv run python scrape_raindrop.py move --all

# Later: discover new bookmarks
uv run python scrape_raindrop.py discover
uv run python scrape_raindrop.py scrape --parallel
```

## Error Handling

- Per-bookmark retry with exponential backoff (1s, 2s, 4s)
- Content validation (< 100 chars = failed)
- **Detailed failure logging** — error reason, detail message, and raw HTML snippet (first 5000 chars) stored in DB
- Error categories: `http_XXX`, `timeout`, `no_content`, `parse_error`, `connection_error`
- Use `fix` command to inspect failure details (human-readable or JSON)
- Graceful Ctrl+C shutdown (preserves DB state, can resume later)
- Use `retry` command to reset failed bookmarks for re-download (clears error fields)
- Re-running `scrape` only processes `pending` bookmarks (already downloaded ones are skipped)

### Medium Auth for Routed Posts

When Raindrop routes Medium URLs to the Medium scraper, member-only posts require authentication. Set `sid` and `uid` cookies in `config.local.toml` under `[medium]` — see the Medium scraper section above for details.
