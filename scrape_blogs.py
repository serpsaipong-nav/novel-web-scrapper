#!/usr/bin/env python3
"""
Databricks Blog Scraper - Scrapes all Databricks blog posts to Obsidian markdown.

Fetches blog post URLs from sitemap XML, then downloads content via Gatsby
page-data JSON endpoints (no browser required). Saves as Obsidian markdown
with tags: clipping, databricks.

Commands:
    discover    Fetch sitemap and add new posts to database
    scrape      Download pending blog posts
    list        List blog posts by status
    status      Show summary statistics
    move        Move downloaded posts to Obsidian vault
    retry       Reset failed posts to pending
    config      View/set configuration
"""

import os
import re
import sys
import json
import time
import signal
import shutil
import tomllib
import argparse
import threading
from pathlib import Path
from datetime import datetime
from xml.etree import ElementTree
from concurrent.futures import ThreadPoolExecutor, as_completed

import duckdb
import requests
from bs4 import BeautifulSoup


# =============================================================================
# Configuration
# =============================================================================

class Config:
    """Configuration manager for blog scraper (reads [blogs] section from TOML)"""

    DEFAULT_CONFIG = {
        'blogs': {
            'staging_dir': 'blogs_obsidian',
            'obsidian_vault': '',
            'database': 'blogs.db',
            'delay': 1.0,
            'max_retries': 3,
            'max_workers': 4,
        }
    }

    def __init__(self, config_dir=None):
        self.config_dir = Path(config_dir) if config_dir else Path.cwd()
        self.config_file = self.config_dir / 'config.toml'
        self.local_config_file = self.config_dir / 'config.local.toml'
        self._config = None

    def load(self):
        """Load configuration from TOML files"""
        config = {}
        for key, val in self.DEFAULT_CONFIG.items():
            config[key] = val.copy() if isinstance(val, dict) else val

        if self.config_file.exists():
            with open(self.config_file, 'rb') as f:
                base = tomllib.load(f)
                self._merge_config(config, base)

        if self.local_config_file.exists():
            with open(self.local_config_file, 'rb') as f:
                local = tomllib.load(f)
                self._merge_config(config, local)

        self._config = config
        return config

    def _merge_config(self, base, override):
        """Deep merge override into base"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._merge_config(base[key], value)
            else:
                base[key] = value

    def get(self, key, default=None):
        """Get a config value from [blogs] section"""
        if self._config is None:
            self.load()
        return self._config.get('blogs', {}).get(key, default)

    def set(self, key, value):
        """Set a config value in local config [blogs] section"""
        local_config = {}
        if self.local_config_file.exists():
            with open(self.local_config_file, 'rb') as f:
                local_config = tomllib.load(f)

        if 'blogs' not in local_config:
            local_config['blogs'] = {}
        local_config['blogs'][key] = value

        self._write_toml(self.local_config_file, local_config)
        self._config = None
        self.load()

    def _write_toml(self, path, config):
        """Write config dict to TOML file"""
        lines = []
        for section, values in config.items():
            if isinstance(values, dict):
                lines.append(f'[{section}]')
                for key, value in values.items():
                    if isinstance(value, str):
                        lines.append(f'{key} = "{value}"')
                    elif isinstance(value, bool):
                        lines.append(f'{key} = {str(value).lower()}')
                    else:
                        lines.append(f'{key} = {value}')
                lines.append('')

        with open(path, 'w') as f:
            f.write('\n'.join(lines))

    @property
    def staging_dir(self):
        return self.get('staging_dir', 'blogs_obsidian')

    @property
    def obsidian_vault(self):
        return self.get('obsidian_vault', '')

    @property
    def database_path(self):
        return self.get('database', 'blogs.db')

    @property
    def delay(self):
        return float(self.get('delay', 1.0))

    @property
    def max_retries(self):
        return int(self.get('max_retries', 3))

    @property
    def max_workers(self):
        return int(self.get('max_workers', 4))


# =============================================================================
# Database
# =============================================================================

class BlogDatabase:
    """DuckDB database manager for tracking blog posts"""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS blog_posts (
        id INTEGER PRIMARY KEY,
        slug VARCHAR NOT NULL UNIQUE,
        url VARCHAR NOT NULL,
        title VARCHAR,
        author VARCHAR,
        publish_date DATE,
        categories VARCHAR,
        word_count INTEGER,
        char_count INTEGER,
        file_path VARCHAR,
        status VARCHAR DEFAULT 'pending',
        downloaded_at TIMESTAMP,
        in_obsidian BOOLEAN DEFAULT FALSE,
        moved_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS blog_sync_logs (
        id INTEGER PRIMARY KEY,
        synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        total_in_sitemap INTEGER,
        new_posts_found INTEGER,
        posts_downloaded INTEGER,
        posts_failed INTEGER,
        status VARCHAR
    );

    CREATE SEQUENCE IF NOT EXISTS blog_posts_id_seq;
    CREATE SEQUENCE IF NOT EXISTS blog_sync_logs_id_seq;
    """

    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = None

    def connect(self):
        """Connect to database and initialize schema"""
        self.conn = duckdb.connect(self.db_path)
        for statement in self.SCHEMA.split(';'):
            statement = statement.strip()
            if statement:
                try:
                    self.conn.execute(statement)
                except Exception:
                    pass
        return self

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def add_post(self, slug, url):
        """Add a new blog post (pending status)"""
        try:
            self.conn.execute("""
                INSERT INTO blog_posts (id, slug, url)
                VALUES (nextval('blog_posts_id_seq'), ?, ?)
            """, [slug, url])
            return True
        except duckdb.ConstraintException:
            return False

    def get_post(self, slug):
        """Get a single post by slug"""
        result = self.conn.execute(
            "SELECT * FROM blog_posts WHERE slug = ?", [slug]
        ).fetchone()
        if result:
            columns = [
                'id', 'slug', 'url', 'title', 'author', 'publish_date',
                'categories', 'word_count', 'char_count', 'file_path',
                'status', 'downloaded_at', 'in_obsidian', 'moved_at', 'created_at'
            ]
            return dict(zip(columns, result))
        return None

    def get_posts(self, status=None, limit=None):
        """Get posts, optionally filtered by status"""
        query = "SELECT id, slug, url, title, author, publish_date, status, file_path, in_obsidian FROM blog_posts"
        params = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY id"
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        results = self.conn.execute(query, params).fetchall()
        columns = ['id', 'slug', 'url', 'title', 'author', 'publish_date', 'status', 'file_path', 'in_obsidian']
        return [dict(zip(columns, row)) for row in results]

    def update_post(self, slug, **kwargs):
        """Update post fields"""
        valid_fields = [
            'title', 'author', 'publish_date', 'categories', 'word_count',
            'char_count', 'file_path', 'status', 'downloaded_at', 'in_obsidian', 'moved_at'
        ]
        updates = []
        values = []
        for field, value in kwargs.items():
            if field in valid_fields:
                updates.append(f"{field} = ?")
                values.append(value)
        if updates:
            values.append(slug)
            self.conn.execute(
                f"UPDATE blog_posts SET {', '.join(updates)} WHERE slug = ?",
                values
            )

    def get_counts(self):
        """Get status counts"""
        results = self.conn.execute("""
            SELECT status, COUNT(*) FROM blog_posts GROUP BY status
        """).fetchall()
        counts = {row[0]: row[1] for row in results}
        total = self.conn.execute("SELECT COUNT(*) FROM blog_posts").fetchone()[0]
        counts['total'] = total
        return counts

    def get_unmoved_posts(self):
        """Get downloaded posts not yet in Obsidian"""
        results = self.conn.execute("""
            SELECT id, slug, url, title, author, publish_date, file_path
            FROM blog_posts
            WHERE status = 'downloaded' AND in_obsidian = FALSE AND file_path IS NOT NULL
            ORDER BY id
        """).fetchall()
        columns = ['id', 'slug', 'url', 'title', 'author', 'publish_date', 'file_path']
        return [dict(zip(columns, row)) for row in results]

    def mark_moved(self, slugs):
        """Mark posts as moved to Obsidian"""
        if not slugs:
            return
        placeholders = ','.join(['?'] * len(slugs))
        self.conn.execute(f"""
            UPDATE blog_posts
            SET in_obsidian = TRUE, moved_at = CURRENT_TIMESTAMP
            WHERE slug IN ({placeholders})
        """, list(slugs))

    def reset_failed(self):
        """Reset failed posts to pending"""
        self.conn.execute("""
            UPDATE blog_posts SET status = 'pending' WHERE status = 'failed'
        """)
        return self.conn.execute(
            "SELECT COUNT(*) FROM blog_posts WHERE status = 'pending'"
        ).fetchone()[0]

    def add_sync_log(self, total_sitemap, new_found, downloaded, failed, status):
        """Add a sync log entry"""
        self.conn.execute("""
            INSERT INTO blog_sync_logs (id, total_in_sitemap, new_posts_found,
                                        posts_downloaded, posts_failed, status)
            VALUES (nextval('blog_sync_logs_id_seq'), ?, ?, ?, ?, ?)
        """, [total_sitemap, new_found, downloaded, failed, status])


# =============================================================================
# Databricks Blog Scraper
# =============================================================================

class DatabricksBlogScraper:
    """Handles sitemap parsing and blog content extraction via Gatsby page-data JSON.

    Databricks blog is a Gatsby site backed by Drupal CMS. Each blog post has a
    static JSON endpoint at a predictable path that contains the full article data
    (title, author, body HTML, categories, etc.) - no browser needed.

    Two URL patterns exist:
      New:    /blog/{slug}          -> /en-blog-assets/page-data/blog/{slug}/page-data.json
      Legacy: /blog/YYYY/MM/DD/X.html -> /blog-legacy-assets/page-data/blog/YYYY/MM/DD/X.html/page-data.json
    """

    SITEMAP_INDEX_URL = 'https://www.databricks.com/webshared/sitemaps/sitemap-index.xml'
    BLOG_URL_PREFIX = 'https://www.databricks.com/blog/'
    BASE_URL = 'https://www.databricks.com'

    # Page-data JSON path templates
    NEW_PAGE_DATA = '/en-blog-assets/page-data/blog/{path}/page-data.json'
    LEGACY_PAGE_DATA = '/blog-legacy-assets/page-data/blog/{path}/page-data.json'

    # Sitemap index entries that contain English blog posts
    BLOG_SITEMAP_KEYWORDS = ['en-blog-assets', 'blog-legacy-assets']

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            ),
        })

    # -------------------------------------------------------------------------
    # Sitemap discovery
    # -------------------------------------------------------------------------

    def fetch_sitemap_urls(self):
        """Fetch all blog post URLs from Databricks sitemaps.

        Walks: sitemap-index.xml -> blog sitemap indexes -> sitemap-0.xml files
        """
        ns = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        all_urls = []

        print("  Fetching sitemap index...")
        top_sitemaps = self._fetch_sitemap_entries(self.SITEMAP_INDEX_URL, ns)
        if not top_sitemaps:
            return []

        blog_indexes = [u for u in top_sitemaps
                        if any(kw in u for kw in self.BLOG_SITEMAP_KEYWORDS)]
        print(f"  Found {len(blog_indexes)} blog sitemap indexes")

        for index_url in blog_indexes:
            print(f"  Fetching: {index_url}")
            sub_sitemaps = self._fetch_sitemap_entries(index_url, ns)
            for sub_url in (sub_sitemaps or []):
                print(f"    Fetching: {sub_url}")
                urls = self._fetch_blog_urls(sub_url, ns)
                all_urls.extend(urls)
                print(f"    -> {len(urls)} blog URLs")

        return all_urls

    def _fetch_sitemap_entries(self, url, ns):
        """Fetch <sitemap><loc> entries from a sitemap index XML"""
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            root = ElementTree.fromstring(response.content)
            return [loc.text.strip() for loc in root.findall('.//sm:sitemap/sm:loc', ns)]
        except Exception as e:
            print(f"  Error fetching {url}: {e}")
            return []

    def _fetch_blog_urls(self, url, ns):
        """Fetch <url><loc> entries from a sitemap and extract blog URLs"""
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            root = ElementTree.fromstring(response.content)
        except Exception as e:
            print(f"  Error fetching {url}: {e}")
            return []

        urls = []
        for loc in root.findall('.//sm:url/sm:loc', ns):
            blog_url = loc.text.strip()
            if blog_url.startswith(self.BLOG_URL_PREFIX):
                slug = self._url_to_slug(blog_url)
                if slug:
                    urls.append({'url': blog_url, 'slug': slug})
        return urls

    def _url_to_slug(self, url):
        """Convert blog URL to a unique slug for the database.

        New:    https://www.databricks.com/blog/some-slug -> some-slug
        Legacy: https://www.databricks.com/blog/2020/09/17/some-slug.html -> 2020-09-17-some-slug
        """
        path = url.replace(self.BLOG_URL_PREFIX, '').strip('/')
        if not path or path.startswith(('page/', 'category/', 'tag/', 'author/')):
            return None

        legacy_match = re.match(r'^(\d{4})/(\d{2})/(\d{2})/(.+?)(?:\.html)?$', path)
        if legacy_match:
            year, month, day, name = legacy_match.groups()
            return f"{year}-{month}-{day}-{name}"

        if '/' not in path:
            return path
        return None

    def _url_to_page_data_urls(self, url):
        """Convert a blog URL to Gatsby page-data JSON URLs (primary + fallback).

        Some new-style URLs are served by the legacy Gatsby app, so we return
        both paths and try them in order.

        Returns list of URLs to try: [primary, fallback]
        """
        path = url.replace(self.BLOG_URL_PREFIX, '').strip('/')

        legacy_match = re.match(r'^(\d{4}/\d{2}/\d{2}/.+\.html)$', path)
        if legacy_match:
            # Legacy URL: try legacy first, then new
            return [
                f"{self.BASE_URL}{self.LEGACY_PAGE_DATA.format(path=path)}",
                f"{self.BASE_URL}{self.NEW_PAGE_DATA.format(path=path)}",
            ]

        # New-style URL: try new first, then legacy
        return [
            f"{self.BASE_URL}{self.NEW_PAGE_DATA.format(path=path)}",
            f"{self.BASE_URL}{self.LEGACY_PAGE_DATA.format(path=path)}",
        ]

    # -------------------------------------------------------------------------
    # Content scraping via page-data JSON
    # -------------------------------------------------------------------------

    def scrape_post(self, url, retries=3):
        """Fetch blog post data from Gatsby page-data JSON endpoint.

        Tries primary page-data URL first, then fallback (some new-style URLs
        are served by the legacy Gatsby app). Returns dict with title, authors,
        date, categories, content or None on failure.
        """
        page_data_urls = self._url_to_page_data_urls(url)

        for page_data_url in page_data_urls:
            result = self._fetch_article(page_data_url, retries)
            if result:
                return result

        return None

    def _fetch_article(self, page_data_url, retries):
        """Fetch and parse a single page-data JSON URL."""
        for attempt in range(retries):
            try:
                response = self.session.get(page_data_url, timeout=30)

                if response.status_code == 404:
                    return None

                response.raise_for_status()
                data = response.json()

                article = (data.get('result', {})
                           .get('data', {})
                           .get('drupal', {})
                           .get('article', {}))

                if not article:
                    return None  # Empty article - try fallback URL

                title = article.get('title')
                if not title:
                    return None

                # Authors (as list of names)
                authors = []
                for author_entry in (article.get('fieldAuthors') or []):
                    entity = (author_entry or {}).get('entity') or {}
                    name = entity.get('name')
                    if name:
                        authors.append(name)

                # Date
                date = None
                raw_date = article.get('entityCreatedRaw') or article.get('fieldDate')
                if raw_date:
                    match = re.match(r'(\d{4}-\d{2}-\d{2})', raw_date)
                    if match:
                        date = match.group(1)

                # Description (from metatags)
                description = ''
                for mt in (article.get('entityMetatags') or []):
                    if isinstance(mt, dict) and mt.get('key') == 'description':
                        description = mt.get('value', '')
                        break

                # AI Summary
                summary = ''
                ai_summary = article.get('fieldAiSummary')
                if isinstance(ai_summary, dict):
                    summary_html = ai_summary.get('processed', '')
                    if summary_html:
                        summary = self._html_to_markdown(summary_html)

                # Categories
                categories = []
                for cat_entry in (article.get('fieldCategories') or []):
                    entity = (cat_entry or {}).get('entity') or {}
                    name = entity.get('name')
                    if name:
                        categories.append(name)
                for tag_entry in (article.get('fieldTags') or []):
                    entity = (tag_entry or {}).get('entity') or {}
                    name = entity.get('name')
                    if name and name not in categories:
                        categories.append(name)
                categories_str = ', '.join(categories) if categories else None

                # Body HTML -> Markdown
                body = article.get('body', {})
                body_html = body.get('processed', '') if isinstance(body, dict) else ''
                if not body_html or len(body_html) < 50:
                    return None

                content = self._html_to_markdown(body_html)
                if not content or len(content) < 100:
                    return None

                return {
                    'title': title,
                    'authors': authors,
                    'date': date,
                    'description': description,
                    'summary': summary,
                    'categories': categories_str,
                    'content': content,
                }

            except requests.exceptions.RequestException as e:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    return None
            except (json.JSONDecodeError, KeyError, TypeError):
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    return None

        return None

    # -------------------------------------------------------------------------
    # HTML to Markdown conversion
    # -------------------------------------------------------------------------

    def _html_to_markdown(self, html):
        """Convert body HTML string to markdown"""
        soup = BeautifulSoup(html, 'html.parser')
        lines = []
        for child in soup.children:
            if hasattr(child, 'name') and child.name:
                self._process_element(child, lines)
            elif child.string and child.string.strip():
                lines.append(child.string.strip())

        text = '\n'.join(lines)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _process_element(self, elem, lines, depth=0):
        """Recursively process HTML elements to markdown"""
        if elem.name is None:
            text = elem.string
            if text and text.strip():
                lines.append(text.strip())
            return

        tag = elem.name.lower() if elem.name else ''

        skip_tags = {'script', 'style', 'nav', 'footer', 'header', 'iframe', 'noscript', 'svg'}
        if tag in skip_tags:
            return

        skip_classes = {'share', 'social', 'sidebar', 'related', 'comment', 'newsletter', 'cta', 'navigation'}
        elem_classes = ' '.join(elem.get('class', [])).lower()
        if any(c in elem_classes for c in skip_classes):
            return

        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            level = int(tag[1])
            text = elem.get_text(strip=True)
            if text:
                lines.append('')
                lines.append(f"{'#' * level} {text}")
                lines.append('')

        elif tag == 'p':
            text = self._inline_to_markdown(elem)
            if text.strip():
                lines.append('')
                lines.append(text)

        elif tag == 'blockquote':
            text = elem.get_text(strip=True)
            if text:
                lines.append('')
                for line in text.split('\n'):
                    line = line.strip()
                    if line:
                        lines.append(f"> {line}")
                lines.append('')

        elif tag in ('ul', 'ol'):
            lines.append('')
            for i, li in enumerate(elem.find_all('li', recursive=False)):
                text = self._inline_to_markdown(li)
                if text.strip():
                    prefix = f"{i+1}." if tag == 'ol' else '-'
                    lines.append(f"{prefix} {text.strip()}")
            lines.append('')

        elif tag == 'pre':
            code = elem.find('code')
            if code:
                lang = ''
                for cls in code.get('class', []):
                    if cls.startswith('language-'):
                        lang = cls.replace('language-', '')
                        break
                lines.append('')
                lines.append(f'```{lang}')
                lines.append(code.get_text())
                lines.append('```')
                lines.append('')
            else:
                lines.append('')
                lines.append('```')
                lines.append(elem.get_text())
                lines.append('```')
                lines.append('')

        elif tag == 'img':
            src = elem.get('src', '')
            alt = elem.get('alt', '')
            if src:
                lines.append(f'![{alt}]({src})')

        elif tag == 'figure':
            img = elem.find('img')
            if img:
                src = img.get('src', '')
                alt = img.get('alt', '')
                if src:
                    lines.append('')
                    lines.append(f'![{alt}]({src})')
                caption = elem.find('figcaption')
                if caption:
                    lines.append(f'*{caption.get_text(strip=True)}*')
                lines.append('')
            else:
                for child in elem.children:
                    if hasattr(child, 'name') and child.name:
                        self._process_element(child, lines, depth + 1)

        elif tag == 'table':
            lines.append('')
            self._table_to_markdown(elem, lines)
            lines.append('')

        elif tag == 'hr':
            lines.append('')
            lines.append('---')
            lines.append('')

        elif tag in ('div', 'section', 'article', 'main', 'span', 'a'):
            for child in elem.children:
                if hasattr(child, 'name') and child.name:
                    self._process_element(child, lines, depth + 1)
                elif child.string and child.string.strip():
                    lines.append(child.string.strip())

        else:
            for child in elem.children:
                if hasattr(child, 'name') and child.name:
                    self._process_element(child, lines, depth + 1)
                elif child.string and child.string.strip():
                    lines.append(child.string.strip())

    def _inline_to_markdown(self, elem):
        """Convert inline HTML to markdown text"""
        parts = []
        for child in elem.children:
            if child.name is None:
                if child.string:
                    parts.append(child.string)
            elif child.name in ('strong', 'b'):
                text = child.get_text()
                if text.strip():
                    parts.append(f'**{text.strip()}**')
            elif child.name in ('em', 'i'):
                text = child.get_text()
                if text.strip():
                    parts.append(f'*{text.strip()}*')
            elif child.name == 'code':
                text = child.get_text()
                if text.strip():
                    parts.append(f'`{text.strip()}`')
            elif child.name == 'a':
                text = child.get_text(strip=True)
                href = child.get('href', '')
                if text and href:
                    parts.append(f'[{text}]({href})')
                elif text:
                    parts.append(text)
            elif child.name == 'br':
                parts.append('\n')
            elif child.name == 'img':
                src = child.get('src', '')
                alt = child.get('alt', '')
                if src:
                    parts.append(f'![{alt}]({src})')
            else:
                text = child.get_text()
                if text:
                    parts.append(text)
        return ''.join(parts)

    def _table_to_markdown(self, table, lines):
        """Convert HTML table to markdown table"""
        rows = []
        for tr in table.find_all('tr'):
            cells = [td.get_text(strip=True).replace('|', '\\|')
                     for td in tr.find_all(['th', 'td'])]
            if cells:
                rows.append(cells)

        if not rows:
            return

        max_cols = max(len(r) for r in rows)
        for row in rows:
            while len(row) < max_cols:
                row.append('')

        lines.append('| ' + ' | '.join(rows[0]) + ' |')
        lines.append('| ' + ' | '.join(['---'] * max_cols) + ' |')
        for row in rows[1:]:
            lines.append('| ' + ' | '.join(row) + ' |')


# =============================================================================
# Blog Manager (Orchestrator)
# =============================================================================

class BlogManager:
    """Orchestrates blog scraping operations"""

    def __init__(self, config=None):
        self.config = config or Config()
        self.config.load()
        self.db = BlogDatabase(self.config.database_path)
        self.scraper = DatabricksBlogScraper()
        self._shutdown = False

    def _setup_signal_handlers(self):
        """Setup graceful shutdown on Ctrl+C"""
        def handler(signum, frame):
            if self._shutdown:
                print("\nForce quit.")
                sys.exit(1)
            print("\nShutting down gracefully (press Ctrl+C again to force)...")
            self._shutdown = True
        signal.signal(signal.SIGINT, handler)

    def discover(self):
        """Fetch sitemap and add new blog post URLs to database"""
        print("Fetching Databricks blog sitemap...")
        urls = self.scraper.fetch_sitemap_urls()

        if not urls:
            print("No URLs found in sitemap.")
            return

        print(f"Found {len(urls)} blog URLs in sitemap")

        new_count = 0
        with self.db:
            for entry in urls:
                added = self.db.add_post(entry['slug'], entry['url'])
                if added:
                    new_count += 1

            counts = self.db.get_counts()

        print(f"New posts added: {new_count}")
        print(f"Total in database: {counts.get('total', 0)}")
        print(f"  Pending: {counts.get('pending', 0)}")
        print(f"  Downloaded: {counts.get('downloaded', 0)}")
        print(f"  Failed: {counts.get('failed', 0)}")

    def scrape(self, slug=None, limit=None, parallel=False):
        """Download blog posts via Gatsby page-data JSON"""
        self._setup_signal_handlers()

        with self.db:
            if slug:
                post = self.db.get_post(slug)
                if not post:
                    print(f"Error: Post with slug '{slug}' not found")
                    print("Run 'discover' first to populate the database")
                    return
                posts = [post]
            else:
                posts = self.db.get_posts(status='pending', limit=limit)

        if not posts:
            print("No pending posts to scrape.")
            return

        if parallel and not slug:
            self._scrape_parallel(posts)
        else:
            self._scrape_sequential(posts)

    def _scrape_sequential(self, posts):
        """Download posts one at a time"""
        print(f"Scraping {len(posts)} blog posts (sequential)")
        print(f"Delay between requests: {self.config.delay}s")
        print()

        successful = 0
        failed = 0

        for i, post in enumerate(posts):
            if self._shutdown:
                print("\nStopping due to shutdown request.")
                break

            print(f"[{i+1}/{len(posts)}] {post['slug']}...", end=" ", flush=True)

            result = self.scraper.scrape_post(
                post['url'], retries=self.config.max_retries
            )

            if result and result['content'] and len(result['content']) >= 100:
                filepath = self._save_post(post['slug'], post['url'], result)

                word_count = len(result['content'].split())
                char_count = len(result['content'])

                author_str = ', '.join(result['authors']) if result['authors'] else None
                with self.db:
                    self.db.update_post(
                        post['slug'],
                        title=result['title'],
                        author=author_str,
                        publish_date=result['date'],
                        categories=result['categories'],
                        word_count=word_count,
                        char_count=char_count,
                        file_path=filepath,
                        status='downloaded',
                        downloaded_at=datetime.now(),
                    )

                print(f"OK ({word_count} words)")
                successful += 1
            else:
                with self.db:
                    self.db.update_post(post['slug'], status='failed')
                print("FAILED (no content)")
                failed += 1

            if i < len(posts) - 1 and not self._shutdown:
                time.sleep(self.config.delay)

        # Log sync
        with self.db:
            status = 'success' if failed == 0 else 'partial'
            self.db.add_sync_log(0, 0, successful, failed, status)

        print(f"\nComplete: {successful} downloaded, {failed} failed")

    def _scrape_parallel(self, posts):
        """Download posts in parallel using ThreadPoolExecutor.

        HTTP fetching and file saving run in parallel threads.
        DB writes are serialized on the main thread to avoid DuckDB
        concurrency issues.
        """
        max_workers = self.config.max_workers
        delay = self.config.delay

        print(f"Scraping {len(posts)} blog posts (parallel, {max_workers} workers)")
        print(f"Delay between requests: {delay}s per worker")
        print()

        successful = 0
        failed = 0
        counter_lock = threading.Lock()
        progress_lock = threading.Lock()
        completed = 0

        def _fetch_post(post):
            """Fetch and save post content (no DB writes)."""
            nonlocal completed

            if self._shutdown:
                return None

            result = self.scraper.scrape_post(
                post['url'], retries=self.config.max_retries
            )

            with counter_lock:
                completed += 1
                idx = completed

            if result and result['content'] and len(result['content']) >= 100:
                filepath = self._save_post(post['slug'], post['url'], result)
                word_count = len(result['content'].split())
                char_count = len(result['content'])

                with progress_lock:
                    print(f"[{idx}/{len(posts)}] {post['slug']}... OK ({word_count} words)")

                time.sleep(delay)
                return {
                    'slug': post['slug'],
                    'success': True,
                    'result': result,
                    'filepath': filepath,
                    'word_count': word_count,
                    'char_count': char_count,
                }
            else:
                with progress_lock:
                    print(f"[{idx}/{len(posts)}] {post['slug']}... FAILED (no content)")

                time.sleep(delay)
                return {
                    'slug': post['slug'],
                    'success': False,
                }

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for post in posts:
                if self._shutdown:
                    break
                future = executor.submit(_fetch_post, post)
                futures[future] = post

            for future in as_completed(futures):
                if self._shutdown:
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

                outcome = future.result()
                if outcome is None:
                    continue

                if outcome['success']:
                    r = outcome['result']
                    author_str = ', '.join(r['authors']) if r['authors'] else None
                    with self.db:
                        self.db.update_post(
                            outcome['slug'],
                            title=r['title'],
                            author=author_str,
                            publish_date=r['date'],
                            categories=r['categories'],
                            word_count=outcome['word_count'],
                            char_count=outcome['char_count'],
                            file_path=outcome['filepath'],
                            status='downloaded',
                            downloaded_at=datetime.now(),
                        )
                    successful += 1
                else:
                    with self.db:
                        self.db.update_post(outcome['slug'], status='failed')
                    failed += 1

        # Log sync
        with self.db:
            status = 'success' if failed == 0 else 'partial'
            self.db.add_sync_log(0, 0, successful, failed, status)

        print(f"\nComplete: {successful} downloaded, {failed} failed")

    def _save_post(self, slug, url, result):
        """Save a blog post as Obsidian markdown matching clipping format.

        Format matches existing Obsidian clippings with:
        - Title-based filename
        - Authors as wikilinks in YAML list
        - published/created date fields
        - description from meta tags
        - #### Summary section with AI-generated bullet points
        """
        output_dir = Path(self.config.staging_dir) / 'Databricks'
        output_dir.mkdir(parents=True, exist_ok=True)

        # Filename based on title (sanitized)
        title = result['title'] or slug
        safe_title = re.sub(r'[<>:"/\\|?*]', '', title)
        safe_title = re.sub(r'\s+', ' ', safe_title).strip()
        filepath = output_dir / f"{safe_title}.md"

        # Escape quotes in title for YAML
        yaml_title = title.replace('"', '\\"')

        date = result['date'] or ''
        today = datetime.now().strftime('%Y-%m-%d')
        description = (result.get('description') or '').replace('"', '\\"')

        # Build frontmatter
        fm = []
        fm.append('---')
        fm.append(f'title: "{yaml_title}"')
        fm.append(f'source: "{url}"')

        # Authors as wikilinks
        if result['authors']:
            fm.append('author:')
            for name in result['authors']:
                fm.append(f'  - "[[{name}]]"')

        if date:
            fm.append(f'published: {date}')
        fm.append(f'created: {today}')
        if description:
            fm.append(f'description: "{description}"')
        fm.append('tags:')
        fm.append('  - "clippings"')
        fm.append('  - "databricks"')
        fm.append('---')

        # Build body
        body_parts = ['\n'.join(fm)]

        # Summary section
        summary = result.get('summary', '')
        if summary:
            body_parts.append('#### Summary')
            body_parts.append('')
            body_parts.append(summary)

        # Main content
        body_parts.append('')
        body_parts.append(result['content'])
        body_parts.append('')

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(body_parts))

        return str(filepath)

    def list_posts(self, status=None, json_output=False):
        """List blog posts"""
        with self.db:
            posts = self.db.get_posts(status=status)

        if json_output:
            print(json.dumps(posts, indent=2, default=str))
            return posts

        if not posts:
            print(f"No posts found{f' with status={status}' if status else ''}.")
            return []

        print(f"\n{'ID':<6} {'Status':<12} {'Date':<12} {'Slug':<50} {'Title'}")
        print('-' * 120)
        for p in posts:
            title = (p.get('title') or '')[:40]
            date = str(p.get('publish_date') or '')[:10]
            print(f"{p['id']:<6} {p['status']:<12} {date:<12} {p['slug'][:49]:<50} {title}")
        print(f"\nTotal: {len(posts)}")
        return posts

    def show_status(self):
        """Show summary statistics"""
        with self.db:
            counts = self.db.get_counts()

        total = counts.get('total', 0)
        pending = counts.get('pending', 0)
        downloaded = counts.get('downloaded', 0)
        failed = counts.get('failed', 0)

        print(f"\nDatabricks Blog Scraper Status")
        print(f"{'='*40}")
        print(f"Total posts:    {total}")
        print(f"  Pending:      {pending}")
        print(f"  Downloaded:   {downloaded}")
        print(f"  Failed:       {failed}")

        if total > 0:
            pct = (downloaded / total) * 100
            print(f"\nProgress: {pct:.1f}%")

        print()

    def move_to_obsidian(self, all_posts=False):
        """Move downloaded posts to Obsidian vault"""
        obsidian_path = self.config.obsidian_vault
        if not obsidian_path:
            print("Error: Obsidian vault path not configured")
            print("Run: scrape_blogs.py config set obsidian_vault /path/to/vault")
            return

        obsidian_path = Path(obsidian_path).expanduser()
        if not obsidian_path.exists():
            print(f"Error: Obsidian vault path does not exist: {obsidian_path}")
            return

        dst_dir = obsidian_path / 'Databricks'
        dst_dir.mkdir(parents=True, exist_ok=True)

        with self.db:
            posts = self.db.get_unmoved_posts()

        if not posts:
            print("No posts to move (all already in Obsidian or none downloaded).")
            return

        moved_slugs = []
        for post in posts:
            src_file = Path(post['file_path'])
            if src_file.exists():
                dst_file = dst_dir / src_file.name
                shutil.copy2(src_file, dst_file)
                moved_slugs.append(post['slug'])

        with self.db:
            self.db.mark_moved(moved_slugs)

        print(f"Moved {len(moved_slugs)} posts to: {dst_dir}")

    def retry_failed(self):
        """Reset failed posts to pending"""
        with self.db:
            count = self.db.reset_failed()
        print(f"Reset failed posts. Pending count: {count}")

    def show_config(self):
        """Display current configuration"""
        print(f"Config file: {self.config.config_file}")
        print(f"Local config: {self.config.local_config_file}")
        print()
        print(f"staging_dir:   {self.config.staging_dir}")
        print(f"obsidian_vault:{self.config.obsidian_vault or '(not set)'}")
        print(f"database:      {self.config.database_path}")
        print(f"delay:         {self.config.delay}")
        print(f"max_retries:   {self.config.max_retries}")

    def set_config(self, key, value):
        """Set a configuration value"""
        if value.lower() in ('true', 'false'):
            value = value.lower() == 'true'
        else:
            try:
                if '.' in value:
                    value = float(value)
                else:
                    value = int(value)
            except ValueError:
                pass

        self.config.set(key, value)
        print(f"Set blogs.{key} = {value}")


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Databricks Blog Scraper - Download blog posts as Obsidian markdown',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Discover blog posts from sitemap
  %(prog)s discover

  # Download all pending posts
  %(prog)s scrape

  # Download a single post
  %(prog)s scrape --slug "delta-lake-explained"

  # Download first 50 pending posts
  %(prog)s scrape --limit 50

  # List posts by status
  %(prog)s list --status downloaded

  # Show statistics
  %(prog)s status

  # Move to Obsidian
  %(prog)s move --all

  # Retry failed posts
  %(prog)s retry

  # Configuration
  %(prog)s config show
  %(prog)s config set obsidian_vault "/path/to/vault"
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Discover
    subparsers.add_parser('discover', help='Fetch sitemap and add new posts to database')

    # Scrape
    scrape_parser = subparsers.add_parser('scrape', help='Download pending blog posts')
    scrape_parser.add_argument('--slug', help='Download a specific post by slug')
    scrape_parser.add_argument('--limit', type=int, help='Limit number of posts to download')
    scrape_parser.add_argument('--parallel', action='store_true',
                               help='Download in parallel (default 4 workers)')
    scrape_parser.add_argument('--sequential', action='store_true',
                               help='Download sequentially (default)')
    scrape_parser.add_argument('--workers', type=int,
                               help='Number of parallel workers (implies --parallel)')

    # List
    list_parser = subparsers.add_parser('list', help='List blog posts')
    list_parser.add_argument('--status', choices=['pending', 'downloaded', 'failed'],
                             help='Filter by status')
    list_parser.add_argument('--json', action='store_true', help='Output as JSON')

    # Status
    subparsers.add_parser('status', help='Show summary statistics')

    # Move
    move_parser = subparsers.add_parser('move', help='Move downloaded posts to Obsidian vault')
    move_parser.add_argument('--all', action='store_true', required=True,
                             help='Move all downloaded posts')

    # Retry
    subparsers.add_parser('retry', help='Reset failed posts to pending')

    # Config
    config_parser = subparsers.add_parser('config', help='View/set configuration')
    config_parser.add_argument('action', choices=['show', 'set'], help='Action')
    config_parser.add_argument('key', nargs='?', help='Config key')
    config_parser.add_argument('value', nargs='?', help='Value to set')

    args = parser.parse_args()

    config = Config()
    config.load()
    manager = BlogManager(config)

    if args.command == 'discover':
        manager.discover()

    elif args.command == 'scrape':
        parallel = args.parallel or bool(args.workers)
        if args.sequential:
            parallel = False
        if args.workers:
            config.set('max_workers', args.workers)
            config.load()
        manager.scrape(slug=args.slug, limit=args.limit, parallel=parallel)

    elif args.command == 'list':
        manager.list_posts(status=args.status, json_output=args.json)

    elif args.command == 'status':
        manager.show_status()

    elif args.command == 'move':
        manager.move_to_obsidian(all_posts=args.all)

    elif args.command == 'retry':
        manager.retry_failed()

    elif args.command == 'config':
        if args.action == 'show':
            manager.show_config()
        elif args.action == 'set':
            if not args.key or args.value is None:
                print("Usage: config set <key> <value>")
                print("Example: config set obsidian_vault /path/to/vault")
                return
            manager.set_config(args.key, args.value)

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
