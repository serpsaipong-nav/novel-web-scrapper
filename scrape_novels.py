#!/usr/bin/env python3
"""
Multi-site Novel Scraper with DuckDB tracking and Obsidian integration.

Supports: lightnovelstranslations.com, freewebnovel.com, webnovel.com, novelbin.com
Features: Parallel downloads, state tracking, Obsidian vault sync, n8n automation ready

Commands:
    add         Add a novel to track
    list        List all tracked novels
    check       Check for new chapters
    sync        Download new chapters
    move        Move chapters to Obsidian vault
    scan-obsidian  Import existing novels from Obsidian
    config      View/set configuration
    scrape      Legacy: scrape chapters directly (one-time)
"""

import os
import re
import sys
import json
import time
import shutil
import random
import tomllib
import argparse
import threading
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import duckdb
import cloudscraper
from bs4 import BeautifulSoup

# Optional Selenium imports (for webnovel.com)
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False


# =============================================================================
# Configuration
# =============================================================================

class Config:
    """Configuration manager with TOML support"""

    DEFAULT_CONFIG = {
        'paths': {
            'staging_dir': 'novels_obsidian',
            'obsidian_vault': '',
            'database': 'novels.db',
        },
        'scraper': {
            'delay': 1.5,
            'max_workers': 4,
            'max_retries': 3,
            'parallel_delay_multiplier': 2.0,
        },
        'notifications': {
            'enabled': False,
            'discord_webhook': '',
            'telegram_bot_token': '',
            'telegram_chat_id': '',
        }
    }

    def __init__(self, config_dir=None):
        self.config_dir = Path(config_dir) if config_dir else Path.cwd()
        self.config_file = self.config_dir / 'config.toml'
        self.local_config_file = self.config_dir / 'config.local.toml'
        self._config = None

    def load(self):
        """Load configuration from TOML files"""
        config = self.DEFAULT_CONFIG.copy()

        # Deep copy nested dicts
        for key in config:
            if isinstance(config[key], dict):
                config[key] = config[key].copy()

        # Load base config
        if self.config_file.exists():
            with open(self.config_file, 'rb') as f:
                base = tomllib.load(f)
                self._merge_config(config, base)

        # Load local overrides
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

    def get(self, section, key, default=None):
        """Get a config value"""
        if self._config is None:
            self.load()
        return self._config.get(section, {}).get(key, default)

    def set(self, section, key, value):
        """Set a config value in local config"""
        # Load existing local config or create new
        local_config = {}
        if self.local_config_file.exists():
            with open(self.local_config_file, 'rb') as f:
                local_config = tomllib.load(f)

        # Set value
        if section not in local_config:
            local_config[section] = {}
        local_config[section][key] = value

        # Write back (convert to TOML format)
        self._write_toml(self.local_config_file, local_config)

        # Reload config
        self._config = None
        self.load()

    def _write_toml(self, path, config):
        """Write config dict to TOML file"""
        lines = []
        for section, values in config.items():
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
        return self.get('paths', 'staging_dir', 'novels_obsidian')

    @property
    def obsidian_vault(self):
        return self.get('paths', 'obsidian_vault', '')

    @property
    def database_path(self):
        return self.get('paths', 'database', 'novels.db')

    @property
    def delay(self):
        return self.get('scraper', 'delay', 1.5)

    @property
    def max_workers(self):
        return self.get('scraper', 'max_workers', 4)

    @property
    def max_retries(self):
        return self.get('scraper', 'max_retries', 3)


# =============================================================================
# Database
# =============================================================================

class Database:
    """DuckDB database manager for tracking novels and chapters"""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS novels (
        id INTEGER PRIMARY KEY,
        name VARCHAR NOT NULL,
        slug VARCHAR NOT NULL,
        url VARCHAR NOT NULL,
        site VARCHAR NOT NULL,
        status VARCHAR DEFAULT 'ongoing',
        total_chapters INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_checked_at TIMESTAMP,
        completed_at TIMESTAMP,
        UNIQUE(slug, site)
    );

    CREATE TABLE IF NOT EXISTS chapters (
        id INTEGER PRIMARY KEY,
        novel_id INTEGER REFERENCES novels(id),
        chapter_num INTEGER NOT NULL,
        title VARCHAR,
        file_path VARCHAR,
        char_count INTEGER,
        downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        in_obsidian BOOLEAN DEFAULT FALSE,
        moved_at TIMESTAMP,
        UNIQUE(novel_id, chapter_num)
    );

    CREATE TABLE IF NOT EXISTS sync_logs (
        id INTEGER PRIMARY KEY,
        novel_id INTEGER REFERENCES novels(id),
        checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        latest_available INTEGER,
        new_chapters_found INTEGER,
        chapters_downloaded INTEGER,
        novel_completed BOOLEAN DEFAULT FALSE,
        status VARCHAR
    );

    CREATE SEQUENCE IF NOT EXISTS novels_id_seq;
    CREATE SEQUENCE IF NOT EXISTS chapters_id_seq;
    CREATE SEQUENCE IF NOT EXISTS sync_logs_id_seq;
    """

    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = None

    def connect(self):
        """Connect to database and initialize schema"""
        self.conn = duckdb.connect(self.db_path)
        # Initialize schema
        for statement in self.SCHEMA.split(';'):
            statement = statement.strip()
            if statement:
                try:
                    self.conn.execute(statement)
                except Exception:
                    pass  # Ignore errors for existing objects
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

    # Novel operations
    def add_novel(self, name, slug, url, site, status='ongoing'):
        """Add a new novel to track"""
        try:
            self.conn.execute("""
                INSERT INTO novels (id, name, slug, url, site, status)
                VALUES (nextval('novels_id_seq'), ?, ?, ?, ?, ?)
            """, [name, slug, url, site, status])
            return self.conn.execute(
                "SELECT id FROM novels WHERE slug = ? AND site = ?", [slug, site]
            ).fetchone()[0]
        except duckdb.ConstraintException:
            # Already exists, return existing ID
            result = self.conn.execute(
                "SELECT id FROM novels WHERE slug = ? AND site = ?", [slug, site]
            ).fetchone()
            return result[0] if result else None

    def get_novel(self, novel_id=None, name=None, slug=None, site=None):
        """Get a novel by ID, name, or slug+site"""
        if novel_id:
            result = self.conn.execute(
                "SELECT * FROM novels WHERE id = ?", [novel_id]
            ).fetchone()
        elif name:
            result = self.conn.execute(
                "SELECT * FROM novels WHERE name = ?", [name]
            ).fetchone()
        elif slug and site:
            result = self.conn.execute(
                "SELECT * FROM novels WHERE slug = ? AND site = ?", [slug, site]
            ).fetchone()
        else:
            return None

        if result:
            columns = ['id', 'name', 'slug', 'url', 'site', 'status',
                       'total_chapters', 'created_at', 'last_checked_at', 'completed_at']
            return dict(zip(columns, result))
        return None

    def list_novels(self):
        """List all tracked novels with chapter counts"""
        results = self.conn.execute("""
            SELECT
                n.id, n.name, n.slug, n.site, n.status, n.url,
                COALESCE(MAX(c.chapter_num), 0) as latest_chapter,
                COUNT(c.id) as chapter_count
            FROM novels n
            LEFT JOIN chapters c ON n.id = c.novel_id
            GROUP BY n.id, n.name, n.slug, n.site, n.status, n.url
            ORDER BY n.name
        """).fetchall()

        novels = []
        for row in results:
            novels.append({
                'id': row[0],
                'name': row[1],
                'slug': row[2],
                'site': row[3],
                'status': row[4],
                'url': row[5],
                'latest_chapter': row[6],
                'chapter_count': row[7],
            })
        return novels

    def update_novel(self, novel_id, **kwargs):
        """Update novel fields"""
        valid_fields = ['name', 'status', 'total_chapters', 'last_checked_at', 'completed_at']
        updates = []
        values = []
        for field, value in kwargs.items():
            if field in valid_fields:
                updates.append(f"{field} = ?")
                values.append(value)

        if updates:
            values.append(novel_id)
            self.conn.execute(
                f"UPDATE novels SET {', '.join(updates)} WHERE id = ?",
                values
            )

    def remove_novel(self, novel_id):
        """Remove a novel and its chapters"""
        self.conn.execute("DELETE FROM chapters WHERE novel_id = ?", [novel_id])
        self.conn.execute("DELETE FROM sync_logs WHERE novel_id = ?", [novel_id])
        self.conn.execute("DELETE FROM novels WHERE id = ?", [novel_id])

    # Chapter operations
    def add_chapter(self, novel_id, chapter_num, title=None, file_path=None, char_count=None):
        """Add a chapter record"""
        try:
            self.conn.execute("""
                INSERT INTO chapters (id, novel_id, chapter_num, title, file_path, char_count)
                VALUES (nextval('chapters_id_seq'), ?, ?, ?, ?, ?)
            """, [novel_id, chapter_num, title, file_path, char_count])
            return True
        except duckdb.ConstraintException:
            # Already exists, update instead
            self.conn.execute("""
                UPDATE chapters
                SET title = COALESCE(?, title),
                    file_path = COALESCE(?, file_path),
                    char_count = COALESCE(?, char_count)
                WHERE novel_id = ? AND chapter_num = ?
            """, [title, file_path, char_count, novel_id, chapter_num])
            return False

    def get_latest_chapter(self, novel_id):
        """Get the latest downloaded chapter number"""
        result = self.conn.execute(
            "SELECT MAX(chapter_num) FROM chapters WHERE novel_id = ?", [novel_id]
        ).fetchone()
        return result[0] if result and result[0] else 0

    def get_chapters(self, novel_id, in_obsidian=None):
        """Get chapters for a novel"""
        query = "SELECT chapter_num, title, file_path, in_obsidian FROM chapters WHERE novel_id = ?"
        params = [novel_id]

        if in_obsidian is not None:
            query += " AND in_obsidian = ?"
            params.append(in_obsidian)

        query += " ORDER BY chapter_num"

        results = self.conn.execute(query, params).fetchall()
        return [{'chapter_num': r[0], 'title': r[1], 'file_path': r[2], 'in_obsidian': r[3]}
                for r in results]

    def mark_chapters_moved(self, novel_id, chapter_nums):
        """Mark chapters as moved to Obsidian"""
        if not chapter_nums:
            return
        placeholders = ','.join(['?'] * len(chapter_nums))
        self.conn.execute(f"""
            UPDATE chapters
            SET in_obsidian = TRUE, moved_at = CURRENT_TIMESTAMP
            WHERE novel_id = ? AND chapter_num IN ({placeholders})
        """, [novel_id] + list(chapter_nums))

    # Sync log operations
    def add_sync_log(self, novel_id, latest_available, new_found, downloaded, status, completed=False):
        """Add a sync log entry"""
        self.conn.execute("""
            INSERT INTO sync_logs (id, novel_id, latest_available, new_chapters_found,
                                   chapters_downloaded, status, novel_completed)
            VALUES (nextval('sync_logs_id_seq'), ?, ?, ?, ?, ?, ?)
        """, [novel_id, latest_available, new_found, downloaded, status, completed])


# =============================================================================
# Base Scraper
# =============================================================================

class NovelScraper:
    """Base scraper class with common functionality"""

    def __init__(self, config: Config):
        self.config = config
        self.output_dir = config.staging_dir
        self.scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'darwin',
                'mobile': False
            }
        )
        self.scraper.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        })
        self.lock = threading.Lock()

    def sanitize_filename(self, name):
        """Convert name to valid filename"""
        clean = re.sub(r'[<>:"/\\|?*]', '', name)
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean

    def to_title_case(self, name):
        """Convert to Title Case for folder names"""
        return ' '.join(word.capitalize() for word in name.split())

    def to_kebab_case(self, name):
        """Convert to kebab-case for tags"""
        clean = re.sub(r'[<>:"/\\|?*\']', '', name)
        clean = re.sub(r'\s+', '-', clean).strip().lower()
        return clean

    def get_folder_name(self, novel_name):
        """Get folder name in Title Case"""
        return self.to_title_case(self.sanitize_filename(novel_name))

    def get_chapter_filename(self, novel_name, chapter_num):
        """Get chapter filename: 0001 - Novel Name.md"""
        folder_name = self.get_folder_name(novel_name)
        return f"{chapter_num:04d} - {folder_name}.md"

    def get_wikilink_name(self, novel_name, chapter_num):
        """Get wikilink format: 0001 - Novel Name"""
        folder_name = self.get_folder_name(novel_name)
        return f"{chapter_num:04d} - {folder_name}"

    def get_index_wikilink_name(self, novel_name):
        folder_name = self.get_folder_name(novel_name)
        return f"{folder_name.replace(' ', '_')}_Index"

    NAV_MARKER = "<!-- nav-footer -->"

    def strip_nav_footer(self, content):
        pattern = re.compile(r'\n\n---\n\n' + re.escape(self.NAV_MARKER) + r'\n[^\n]*\n?$')
        return pattern.sub('', content).rstrip('\n')

    def build_nav_footer(self, novel_name, chapter_nums, i):
        index_wikilink = self.get_index_wikilink_name(novel_name)
        prev_link = (
            f"[[{self.get_wikilink_name(novel_name, chapter_nums[i-1])}|← Ch {chapter_nums[i-1]}]]"
            if i > 0 else "*(first)*"
        )
        next_link = (
            f"[[{self.get_wikilink_name(novel_name, chapter_nums[i+1])}|Ch {chapter_nums[i+1]} →]]"
            if i < len(chapter_nums) - 1 else "*(last)*"
        )
        index_link = f"[[{index_wikilink}|Index]]"
        return f"\n\n---\n\n{self.NAV_MARKER}\n{prev_link} | {index_link} | {next_link}\n"

    def update_chapter_navigation(self, novel_name, chapter_nums, target_dir=None):
        """Add/update prev/next/index nav footer on all chapter files in target_dir."""
        folder_name = self.get_folder_name(novel_name)
        base = Path(target_dir) if target_dir else Path(self.output_dir)
        novel_dir = base / folder_name

        if not novel_dir.exists():
            return

        chapter_nums = sorted(chapter_nums)
        for i, num in enumerate(chapter_nums):
            filepath = novel_dir / self.get_chapter_filename(novel_name, num)
            if not filepath.exists():
                continue
            content = filepath.read_text(encoding='utf-8')
            content = self.strip_nav_footer(content)
            content += self.build_nav_footer(novel_name, chapter_nums, i)
            filepath.write_text(content, encoding='utf-8')

    def save_chapter(self, novel_name, chapter_num, title, content):
        """Save chapter in Obsidian format"""
        folder_name = self.get_folder_name(novel_name)
        novel_dir = os.path.join(self.output_dir, folder_name)
        os.makedirs(novel_dir, exist_ok=True)

        filename = self.get_chapter_filename(novel_name, chapter_num)
        filepath = os.path.join(novel_dir, filename)

        tag_slug = self.to_kebab_case(novel_name)

        markdown_content = f"""---
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

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(markdown_content)

        return filepath, len(content)

    def create_index_file(self, novel_name, chapter_nums):
        """Create index file with links to all chapters"""
        folder_name = self.get_folder_name(novel_name)
        novel_dir = os.path.join(self.output_dir, folder_name)

        if not os.path.exists(novel_dir):
            return None

        tag_slug = self.to_kebab_case(novel_name)
        index_filename = f"{folder_name.replace(' ', '_')}_Index.md"
        index_filepath = os.path.join(novel_dir, index_filename)

        chapter_nums = sorted(chapter_nums)

        toc_lines = []
        for num in chapter_nums:
            wikilink = self.get_wikilink_name(novel_name, num)
            toc_lines.append(f"- [[{wikilink}|Chapter {num}]]")

        toc_content = '\n'.join(toc_lines)

        index_content = f"""---
tags:
  - book/novel
  - {tag_slug}
---

# {folder_name}

## Table of Contents

{toc_content}
"""

        with open(index_filepath, 'w', encoding='utf-8') as f:
            f.write(index_content)

        return index_filepath

    # Methods to override in subclasses
    def get_chapter_list(self, novel_slug):
        """Fetch chapter list from website. Returns list of dicts with 'num', 'url', 'title'"""
        raise NotImplementedError

    def scrape_chapter_by_url(self, url, retries=3):
        """Scrape a single chapter. Returns (title, content) or (None, None)"""
        raise NotImplementedError

    def get_novel_status(self, novel_slug):
        """Check if novel is completed. Returns 'ongoing', 'completed', or 'hiatus'"""
        return 'ongoing'

    def get_latest_chapter_num(self, novel_slug):
        """Get the latest available chapter number from website"""
        chapters = self.get_chapter_list(novel_slug)
        if chapters:
            return max(c['num'] for c in chapters)
        return 0


# =============================================================================
# Site-Specific Scrapers
# =============================================================================

class LightNovelTranslationsScraper(NovelScraper):
    """Scraper for lightnovelstranslations.com"""

    SITE = 'lightnovelstranslations.com'
    BASE_URL = 'https://lightnovelstranslations.com'

    def get_chapter_list(self, novel_slug):
        """Fetch all chapter URLs from the table of contents page"""
        toc_url = f"{self.BASE_URL}/novel/{novel_slug}/?tab=table_contents"

        try:
            response = self.scraper.get(toc_url, timeout=30)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')

                chapters = []
                links = soup.find_all('a', href=True)

                for link in links:
                    href = link.get('href', '')
                    if f'/novel/{novel_slug}/' in href and 'tab=' not in href:
                        text = link.get_text(strip=True)
                        if not text:
                            continue

                        chapter_num = None
                        match = re.search(r'Chapter\s*(\d+)', text, re.IGNORECASE)
                        if match:
                            chapter_num = int(match.group(1))
                        else:
                            url_match = re.search(r'chapter-?(\d+)', href, re.IGNORECASE)
                            if url_match:
                                chapter_num = int(url_match.group(1))

                        if chapter_num is not None:
                            chapters.append({
                                'num': chapter_num,
                                'url': href,
                                'title': link.get('title', text) or text
                            })

                # Remove duplicates
                seen = set()
                unique = []
                for ch in chapters:
                    if ch['num'] not in seen:
                        seen.add(ch['num'])
                        unique.append(ch)

                unique.sort(key=lambda x: x['num'])
                return unique
        except Exception as e:
            print(f"Error fetching chapter list: {e}")

        return []

    def scrape_chapter_by_url(self, url, retries=None):
        """Scrape a single chapter by URL"""
        retries = retries or self.config.max_retries

        for attempt in range(retries):
            try:
                response = self.scraper.get(url, timeout=30)

                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'html.parser')

                    # Get title
                    title = "Chapter"
                    for selector in ["h2", "h1.entry-title", ".entry-title", "h1"]:
                        elem = soup.select_one(selector)
                        if elem and elem.get_text(strip=True):
                            title = elem.get_text(strip=True)
                            break

                    # Get content
                    content = ""
                    for selector in [".text_story", ".entry-content", ".post-content", ".chapter-content", "article"]:
                        elem = soup.select_one(selector)
                        if elem:
                            paragraphs = elem.find_all('p')
                            if paragraphs:
                                parts = []
                                for p in paragraphs:
                                    text = p.get_text(strip=True)
                                    if len(text) > 15:
                                        skip = ['prev chapter', 'next chapter', 'translator:',
                                                'editor:', 'patreon', 'kofi', 'adsbygoogle',
                                                'bookmark', 'comment', 'report', 'login']
                                        if not any(s in text.lower() for s in skip):
                                            parts.append(text)
                                if parts:
                                    content = '\n\n'.join(parts)
                                    break

                    if content and len(content) > 50:
                        return title, content

                elif response.status_code == 404:
                    return None, None

            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(2)

        return None, None

    def get_novel_status(self, novel_slug):
        """Check novel status from the page"""
        url = f"{self.BASE_URL}/novel/{novel_slug}/"
        try:
            response = self.scraper.get(url, timeout=30)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                text = soup.get_text().lower()
                if 'completed' in text or 'complete' in text:
                    return 'completed'
                if 'hiatus' in text:
                    return 'hiatus'
        except Exception:
            pass
        return 'ongoing'


class FreeWebNovelScraper(NovelScraper):
    """Scraper for freewebnovel.com"""

    SITE = 'freewebnovel.com'
    BASE_URL = 'https://freewebnovel.com'

    def get_chapter_list(self, novel_slug):
        """Generate chapter list (sequential URLs)"""
        # FreeWebNovel uses sequential chapters, we need to find the max
        # Try to get from the novel page
        url = f"{self.BASE_URL}/{novel_slug}.html"
        try:
            response = self.scraper.get(url, timeout=30)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                links = soup.find_all('a', href=True)
                max_chapter = 0
                for link in links:
                    href = link.get('href', '')
                    match = re.search(r'chapter-(\d+)', href)
                    if match:
                        max_chapter = max(max_chapter, int(match.group(1)))

                if max_chapter > 0:
                    return [{'num': i, 'url': f"{self.BASE_URL}/{novel_slug}/chapter-{i}.html",
                             'title': f"Chapter {i}"} for i in range(1, max_chapter + 1)]
        except Exception:
            pass
        return []

    def scrape_chapter_by_url(self, url, retries=None):
        """Scrape a single chapter"""
        retries = retries or self.config.max_retries

        for attempt in range(retries):
            try:
                response = self.scraper.get(url, timeout=30)

                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'html.parser')

                    title = "Chapter"
                    elem = soup.select_one('h1.tit, .chapter-title, h1')
                    if elem:
                        title = elem.get_text(strip=True)

                    parts = []
                    for p in soup.find_all('p'):
                        text = p.get_text(strip=True)
                        if len(text) > 15:
                            skip = ['prev chapter', 'next chapter', 'freewebnovel.com',
                                    'report chapter', 'tap the screen', 'log in']
                            if not any(s in text.lower() for s in skip):
                                parts.append(text)

                    if parts:
                        content = '\n\n'.join(parts)
                        if len(content) > 100:
                            return title, content

                elif response.status_code == 404:
                    return None, None

            except Exception:
                if attempt < retries - 1:
                    time.sleep(2)

        return None, None


class NovelBinScraper(NovelScraper):
    """Scraper for novelbin.com"""

    SITE = 'novelbin.com'
    BASE_URL = 'https://novelbin.com'

    def get_chapter_list(self, novel_slug):
        """Fetch chapter list from AJAX endpoint"""
        ajax_url = f"{self.BASE_URL}/ajax/chapter-archive?novelId={novel_slug}"

        try:
            response = self.scraper.get(ajax_url, timeout=30)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                links = soup.select('a[href*="chapter"]')

                chapters = []
                for link in links:
                    href = link.get('href', '')
                    text = link.get_text(strip=True)
                    match = re.search(r'Chapter\s+(\d+)', text, re.IGNORECASE)
                    if match:
                        chapters.append({
                            'num': int(match.group(1)),
                            'url': href,
                            'title': text
                        })

                chapters.sort(key=lambda x: x['num'])
                return chapters
        except Exception:
            pass
        return []

    def scrape_chapter_by_url(self, url, retries=None):
        """Scrape a single chapter"""
        retries = retries or self.config.max_retries

        for attempt in range(retries):
            try:
                response = self.scraper.get(url, timeout=30)

                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'html.parser')

                    title = "Chapter"
                    for selector in [".chr-title", "h2 a.chr-title", "h1"]:
                        elem = soup.select_one(selector)
                        if elem and elem.get_text(strip=True):
                            title = elem.get_text(strip=True)
                            break

                    content = ""
                    for selector in ["#chr-content", ".chr-c", "#chapter-content"]:
                        elem = soup.select_one(selector)
                        if elem:
                            parts = []
                            for p in elem.find_all('p'):
                                text = p.get_text(strip=True)
                                if len(text) > 15:
                                    skip = ['prev', 'next', 'comment', 'novelbin']
                                    if not any(s in text.lower()[:50] for s in skip):
                                        parts.append(text)
                            if parts:
                                content = '\n\n'.join(parts)
                                break

                    if content and len(content) > 50:
                        return title, content

                elif response.status_code == 404:
                    return None, None

            except Exception:
                if attempt < retries - 1:
                    time.sleep(3)

        return None, None


class WebNovelScraper(NovelScraper):
    """Scraper for webnovel.com using Playwright (site is fully JS-rendered).

    Free chapters are scraped via API interception; locked/VIP chapters return
    (None, None) and are silently skipped.
    """

    SITE = 'webnovel.com'
    BASE_URL = 'https://www.webnovel.com'

    @staticmethod
    def _sync_playwright():
        try:
            from playwright.sync_api import sync_playwright
            return sync_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright is required for webnovel.com. "
                "Run: uv run playwright install chromium"
            )

    def get_chapter_list(self, novel_slug):
        """Load catalog page via Playwright, intercept API response for chapter list."""
        sync_playwright = self._sync_playwright()
        chapters = []

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                captured = []

                def on_response(response):
                    try:
                        if response.status == 200 and response.request.resource_type in ('xhr', 'fetch'):
                            data = response.json()
                            if isinstance(data, dict) and 'volumeItems' in str(data):
                                captured.append(data)
                    except Exception:
                        pass

                page.on('response', on_response)
                page.goto(
                    f"{self.BASE_URL}/book/{novel_slug}/catalog",
                    wait_until='load', timeout=60000
                )
                # Wait for chapter data to appear (API call completes after initial load)
                try:
                    page.wait_for_function(
                        "() => document.querySelector('[class*=\"chapter\"]') !== null",
                        timeout=15000
                    )
                except Exception:
                    pass

                # Parse from intercepted API response
                for data in captured:
                    for volume in data.get('data', {}).get('volumeItems', []):
                        for ch in volume.get('chapterItems', []):
                            ch_id = str(ch.get('id', ''))
                            ch_index = ch.get('index', 0)
                            if ch_id and ch_index:
                                chapters.append({
                                    'num': ch_index,
                                    'url': f"{self.BASE_URL}/book/{novel_slug}/{ch_id}",
                                    'title': ch.get('name', f"Chapter {ch_index}"),
                                })

                # DOM fallback: look for rendered chapter links
                if not chapters:
                    try:
                        links = page.eval_on_selector_all(
                            'a[href*="/book/"]',
                            'els => els.map(e => ({href: e.href, text: e.innerText.trim()}))'
                        )
                        for link in links:
                            href = link.get('href', '')
                            text = link.get('text', '')
                            ch_match = re.search(r'/book/\d+/(\d+)', href)
                            num_match = re.search(r'[Cc]hapter\s*(\d+)', text)
                            if ch_match and num_match:
                                chapters.append({
                                    'num': int(num_match.group(1)),
                                    'url': href,
                                    'title': text or f"Chapter {num_match.group(1)}",
                                })
                    except Exception:
                        pass

                browser.close()
        except Exception as e:
            print(f"Error fetching chapter list: {e}")

        chapters.sort(key=lambda x: x['num'])
        seen: set = set()
        return [ch for ch in chapters if not (ch['num'] in seen or seen.add(ch['num']))]

    def scrape_chapter_by_url(self, url, retries=None):
        """Render chapter page via Playwright; extract from API intercept or DOM."""
        sync_playwright = self._sync_playwright()
        retries = retries or self.config.max_retries

        for attempt in range(retries):
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    page = browser.new_page()
                    captured = []

                    def on_response(response):
                        try:
                            if response.status == 200 and response.request.resource_type in ('xhr', 'fetch'):
                                data = response.json()
                                if isinstance(data, dict) and data.get('code') == 0 and 'chapterInfo' in str(data):
                                    captured.append(data)
                        except Exception:
                            pass

                    page.on('response', on_response)
                    page.goto(url, wait_until='load', timeout=60000)
                    # Wait for chapter content to render
                    try:
                        page.wait_for_selector('.cha-words, .chapter-content', timeout=15000)
                    except Exception:
                        pass

                    title = 'Chapter'
                    content = ''

                    if captured:
                        ch_info = captured[0].get('data', {}).get('chapterInfo', {})
                        title = ch_info.get('chapterName', 'Chapter')
                        content = '\n\n'.join(
                            item.get('content', '').strip()
                            for item in ch_info.get('contents', [])
                            if item.get('content', '').strip()
                        )
                    else:
                        # DOM fallback
                        try:
                            page.wait_for_selector('.cha-words, .chapter-content', timeout=15000)
                        except Exception:
                            pass

                        for sel in ['h3.cha-tit', '.chapter-title h3', 'h1']:
                            elem = page.query_selector(sel)
                            if elem:
                                title = elem.inner_text().strip()
                                break

                        for sel in ['.cha-words p', '.cha-content p', '.chapter-content p']:
                            elems = page.query_selector_all(sel)
                            if elems:
                                parts = [e.inner_text().strip() for e in elems if len(e.inner_text().strip()) > 15]
                                if parts:
                                    content = '\n\n'.join(parts)
                                    break

                    browser.close()

                    if content and len(content) > 50:
                        return title, content
                    # Locked/VIP chapter — skip silently
                    return None, None

            except Exception:
                if attempt < retries - 1:
                    time.sleep(2)

        return None, None

    def get_novel_status(self, novel_slug):
        # Meta description from catalog page is available without JS
        try:
            r = self.scraper.get(f"{self.BASE_URL}/book/{novel_slug}/catalog", timeout=30)
            if r.status_code == 200:
                m = re.search(r'<meta name="description"[^>]*content="([^"]*)"', r.text)
                if m and 'complet' in m.group(1).lower():
                    return 'completed'
        except Exception:
            pass
        return 'ongoing'


# Scraper registry
SCRAPERS = {
    'lightnovelstranslations.com': LightNovelTranslationsScraper,
    'freewebnovel.com': FreeWebNovelScraper,
    'novelbin.com': NovelBinScraper,
    'webnovel.com': WebNovelScraper,
}


def get_scraper_for_url(url, config):
    """Get the appropriate scraper for a URL"""
    url_lower = url.lower()
    for site, scraper_class in SCRAPERS.items():
        if site in url_lower:
            return scraper_class(config), site
    return None, None


def extract_slug_from_url(url, site):
    """Extract novel slug from URL"""
    if 'lightnovelstranslations.com' in site:
        match = re.search(r'/novel/([^/?]+)', url)
        return match.group(1) if match else None
    elif 'freewebnovel.com' in site:
        slug = url.rstrip('/').split('/')[-1].replace('.html', '')
        if slug.startswith('chapter-'):
            slug = url.rstrip('/').split('/')[-2]
        return slug
    elif 'novelbin.com' in site:
        match = re.search(r'/b/([^/]+)', url)
        return match.group(1) if match else None
    elif 'webnovel.com' in site:
        # URL: https://www.webnovel.com/book/title_BOOKID
        match = re.search(r'_(\d+)(?:/|$)', url)
        return match.group(1) if match else None
    return None


# =============================================================================
# Novel Manager (Orchestrates everything)
# =============================================================================

class NovelManager:
    """Main manager for novel scraping operations"""

    def __init__(self, config: Config = None):
        self.config = config or Config()
        self.config.load()
        self.db = Database(self.config.database_path)

    def add_novel(self, url, name):
        """Add a new novel to track"""
        scraper, site = get_scraper_for_url(url, self.config)
        if not scraper:
            print(f"Error: Unsupported site for URL: {url}")
            return None

        slug = extract_slug_from_url(url, site)
        if not slug:
            print(f"Error: Could not extract novel slug from URL")
            return None

        with self.db:
            novel_id = self.db.add_novel(name, slug, url, site)
            print(f"Added novel: {name} (ID: {novel_id})")
            return novel_id

    def list_novels(self, json_output=False):
        """List all tracked novels"""
        with self.db:
            novels = self.db.list_novels()

        if json_output:
            print(json.dumps(novels, indent=2, default=str))
            return novels

        if not novels:
            print("No novels being tracked. Use 'add' to add a novel.")
            return []

        print(f"\n{'ID':<4} {'Name':<35} {'Status':<10} {'Chapters':<10} {'Site'}")
        print("-" * 80)
        for n in novels:
            print(f"{n['id']:<4} {n['name'][:34]:<35} {n['status']:<10} {n['latest_chapter']:<10} {n['site']}")
        print()
        return novels

    def remove_novel(self, name):
        """Remove a novel from tracking"""
        with self.db:
            novel = self.db.get_novel(name=name)
            if not novel:
                print(f"Error: Novel '{name}' not found")
                return False

            self.db.remove_novel(novel['id'])
            print(f"Removed novel: {name}")
            return True

    def check_novels(self, name=None, json_output=False):
        """Check for new chapters"""
        with self.db:
            if name:
                novel = self.db.get_novel(name=name)
                if not novel:
                    print(f"Error: Novel '{name}' not found")
                    return []
                novels = [novel]
            else:
                novels = self.db.list_novels()

        results = []
        for novel in novels:
            scraper, _ = get_scraper_for_url(novel['url'], self.config)
            if not scraper:
                continue

            print(f"Checking: {novel['name']}...", end=" ", flush=True)

            chapters = scraper.get_chapter_list(novel['slug'])
            latest_available = max(c['num'] for c in chapters) if chapters else 0

            with self.db:
                latest_local = self.db.get_latest_chapter(novel['id'])

            new_count = max(0, latest_available - latest_local)
            status = scraper.get_novel_status(novel['slug'])

            result = {
                'name': novel['name'],
                'local': latest_local,
                'available': latest_available,
                'new': new_count,
                'status': status,
            }
            results.append(result)

            if new_count > 0:
                print(f"{latest_local} local, {latest_available} available (+{new_count} new) [{status}]")
            else:
                print(f"up to date ({latest_local}) [{status}]")

            # Update last checked
            with self.db:
                self.db.update_novel(novel['id'], last_checked_at=datetime.now(), status=status)
                if status == 'completed':
                    self.db.update_novel(novel['id'], completed_at=datetime.now(),
                                         total_chapters=latest_available)

        if json_output:
            print(json.dumps(results, indent=2))

        return results

    def sync_novel(self, name=None, all_novels=False, parallel=True):
        """Download new chapters"""
        with self.db:
            if all_novels:
                novels = self.db.list_novels()
            elif name:
                novel = self.db.get_novel(name=name)
                if not novel:
                    print(f"Error: Novel '{name}' not found")
                    return
                novels = [novel]
            else:
                print("Error: Specify --name or --all")
                return

        for novel in novels:
            self._sync_single_novel(novel, parallel)

    def _sync_single_novel(self, novel, parallel=True):
        """Sync a single novel"""
        scraper, _ = get_scraper_for_url(novel['url'], self.config)
        if not scraper:
            print(f"Error: No scraper for {novel['name']}")
            return

        print(f"\n{'='*60}")
        print(f"Syncing: {novel['name']}")
        print(f"{'='*60}")

        # Get chapter list
        chapters = scraper.get_chapter_list(novel['slug'])
        if not chapters:
            print("Failed to fetch chapter list")
            return

        # Find new chapters
        with self.db:
            latest_local = self.db.get_latest_chapter(novel['id'])

        new_chapters = [c for c in chapters if c['num'] > latest_local]

        if not new_chapters:
            print("No new chapters")
            return

        print(f"Found {len(new_chapters)} new chapters ({new_chapters[0]['num']} to {new_chapters[-1]['num']})")

        # Download
        if parallel and len(new_chapters) > 1:
            successful, failed = self._download_parallel(scraper, novel, new_chapters)
        else:
            successful, failed = self._download_sequential(scraper, novel, new_chapters)

        # Create index and update navigation
        if successful > 0:
            with self.db:
                all_chapters = self.db.get_chapters(novel['id'])
                chapter_nums = [c['chapter_num'] for c in all_chapters]
            scraper.create_index_file(novel['name'], chapter_nums)
            scraper.update_chapter_navigation(novel['name'], chapter_nums)

        # Log sync
        with self.db:
            latest_available = max(c['num'] for c in chapters)
            status = 'success' if failed == 0 else 'partial'
            self.db.add_sync_log(novel['id'], latest_available, len(new_chapters),
                                 successful, status)

        print(f"\nComplete: {successful} downloaded, {failed} failed")

    def _download_parallel(self, scraper, novel, chapters):
        """Download chapters in parallel"""
        max_workers = self.config.max_workers
        delay = self.config.delay * self.config.get('scraper', 'parallel_delay_multiplier', 2.0)

        print(f"Downloading with {max_workers} workers (delay: {delay}s)")

        successful = 0
        failed = 0
        results = {}

        def download_chapter(chapter):
            # Add jitter
            time.sleep(random.uniform(delay * 0.7, delay * 1.3))

            title, content = scraper.scrape_chapter_by_url(chapter['url'])
            if content:
                filepath, char_count = scraper.save_chapter(novel['name'], chapter['num'], title, content)
                return chapter['num'], True, title, filepath, char_count
            return chapter['num'], False, None, None, None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(download_chapter, ch): ch for ch in chapters}

            for i, future in enumerate(as_completed(futures)):
                chapter_num, success, title, filepath, char_count = future.result()

                if success:
                    with self.db:
                        self.db.add_chapter(novel['id'], chapter_num, title, filepath, char_count)
                    successful += 1
                    print(f"[{i+1}/{len(chapters)}] Chapter {chapter_num}: OK ({char_count} chars)")
                else:
                    failed += 1
                    print(f"[{i+1}/{len(chapters)}] Chapter {chapter_num}: FAILED")

        return successful, failed

    def _download_sequential(self, scraper, novel, chapters):
        """Download chapters sequentially"""
        delay = self.config.delay
        successful = 0
        failed = 0

        for i, chapter in enumerate(chapters):
            print(f"[{i+1}/{len(chapters)}] Chapter {chapter['num']}...", end=" ", flush=True)

            title, content = scraper.scrape_chapter_by_url(chapter['url'])

            if content:
                filepath, char_count = scraper.save_chapter(novel['name'], chapter['num'], title, content)
                with self.db:
                    self.db.add_chapter(novel['id'], chapter['num'], title, filepath, char_count)
                print(f"OK ({char_count} chars)")
                successful += 1
            else:
                print("FAILED")
                failed += 1

            if i < len(chapters) - 1:
                time.sleep(delay)

        return successful, failed

    def move_to_obsidian(self, name=None, all_novels=False):
        """Move downloaded chapters to Obsidian vault"""
        obsidian_path = self.config.obsidian_vault
        if not obsidian_path:
            print("Error: Obsidian vault path not configured")
            print("Run: scrape_novels.py config set obsidian_vault /path/to/vault")
            return

        obsidian_path = Path(obsidian_path).expanduser()
        if not obsidian_path.exists():
            print(f"Error: Obsidian vault path does not exist: {obsidian_path}")
            return

        with self.db:
            if all_novels:
                novels = self.db.list_novels()
            elif name:
                novel = self.db.get_novel(name=name)
                if not novel:
                    print(f"Error: Novel '{name}' not found")
                    return
                novels = [novel]
            else:
                print("Error: Specify --name or --all")
                return

        for novel in novels:
            self._move_novel_to_obsidian(novel, obsidian_path)

    def _move_novel_to_obsidian(self, novel, obsidian_path):
        """Move a single novel to Obsidian"""
        scraper = NovelScraper(self.config)  # Just for folder name helper
        folder_name = scraper.get_folder_name(novel['name'])

        src_dir = Path(self.config.staging_dir) / folder_name
        dst_dir = obsidian_path / folder_name

        if not src_dir.exists():
            print(f"No files to move for: {novel['name']}")
            return

        # Get chapters not yet in Obsidian
        with self.db:
            chapters = self.db.get_chapters(novel['id'], in_obsidian=False)

        if not chapters:
            print(f"All chapters already in Obsidian: {novel['name']}")
            return

        # Create destination if needed
        dst_dir.mkdir(parents=True, exist_ok=True)

        moved = []
        for chapter in chapters:
            if chapter['file_path']:
                src_file = Path(chapter['file_path'])
                if src_file.exists():
                    dst_file = dst_dir / src_file.name
                    shutil.copy2(src_file, dst_file)
                    moved.append(chapter['chapter_num'])

        # Also copy index file
        index_name = f"{folder_name.replace(' ', '_')}_Index.md"
        src_index = src_dir / index_name
        if src_index.exists():
            shutil.copy2(src_index, dst_dir / index_name)

        # Mark as moved
        with self.db:
            self.db.mark_chapters_moved(novel['id'], moved)

        # Rebuild nav in vault for all chapters (covers boundary prev/next after new batch)
        with self.db:
            all_chapters = self.db.get_chapters(novel['id'])
            all_chapter_nums = [c['chapter_num'] for c in all_chapters]
        scraper = NovelScraper(self.config)
        scraper.update_chapter_navigation(novel['name'], all_chapter_nums, target_dir=obsidian_path)

        print(f"Moved {len(moved)} chapters to Obsidian: {novel['name']}")

    def scan_obsidian(self):
        """Scan Obsidian vault and import existing novels"""
        obsidian_path = self.config.obsidian_vault
        if not obsidian_path:
            print("Error: Obsidian vault path not configured")
            return

        obsidian_path = Path(obsidian_path).expanduser()
        if not obsidian_path.exists():
            print(f"Error: Path does not exist: {obsidian_path}")
            return

        print(f"Scanning: {obsidian_path}\n")

        imported_novels = 0
        imported_chapters = 0

        for novel_dir in obsidian_path.iterdir():
            if not novel_dir.is_dir():
                continue

            # Find chapter files
            chapter_files = list(novel_dir.glob("[0-9][0-9][0-9][0-9] - *.md"))
            if not chapter_files:
                continue

            novel_name = novel_dir.name
            print(f"Found: {novel_name}/")

            # Extract chapter numbers
            chapter_nums = []
            for f in chapter_files:
                match = re.match(r'(\d{4}) - ', f.name)
                if match:
                    chapter_nums.append(int(match.group(1)))

            if not chapter_nums:
                continue

            chapter_nums.sort()
            print(f"  - {len(chapter_nums)} chapters ({min(chapter_nums)}-{max(chapter_nums)})")

            # Add to database or update existing
            with self.db:
                # Check if novel already exists (by name)
                novel = self.db.get_novel(name=novel_name)

                if not novel:
                    # Add new novel (imported without URL)
                    try:
                        self.db.conn.execute("""
                            INSERT INTO novels (id, name, slug, url, site, status)
                            VALUES (nextval('novels_id_seq'), ?, ?, ?, ?, ?)
                        """, [novel_name, novel_name.lower().replace(' ', '-'),
                              '', 'imported', 'unknown'])
                        novel = self.db.get_novel(name=novel_name)
                    except duckdb.ConstraintException:
                        pass

                if novel:
                    for num in chapter_nums:
                        filepath = str(novel_dir / f"{num:04d} - {novel_name}.md")
                        self.db.add_chapter(novel['id'], num, None, filepath, None)
                        self.db.mark_chapters_moved(novel['id'], [num])
                    imported_chapters += len(chapter_nums)

            imported_novels += 1

        print(f"\nImported: {imported_novels} novels, {imported_chapters} chapters")

    def nav_update(self, name=None, all_novels=False, vault=False):
        """Rebuild prev/next/index navigation for chapters in staging or vault."""
        target_dir = None
        if vault:
            obsidian_path = self.config.obsidian_vault
            if not obsidian_path:
                print("Error: Obsidian vault path not configured")
                return
            target_dir = str(Path(obsidian_path).expanduser())

        with self.db:
            if all_novels:
                novels = self.db.list_novels()
            elif name:
                novel = self.db.get_novel(name=name)
                if not novel:
                    print(f"Error: Novel '{name}' not found")
                    return
                novels = [novel]
            else:
                print("Error: Specify --name or --all")
                return

        scraper = NovelScraper(self.config)
        for novel in novels:
            with self.db:
                chapters = self.db.get_chapters(novel['id'])
            chapter_nums = [c['chapter_num'] for c in chapters]
            if not chapter_nums:
                print(f"No chapters tracked for: {novel['name']}")
                continue
            scraper.create_index_file(novel['name'], chapter_nums)
            scraper.update_chapter_navigation(novel['name'], chapter_nums, target_dir=target_dir)
            location = f"vault ({target_dir})" if vault else "staging"
            print(f"Updated nav for {novel['name']}: {len(chapter_nums)} chapters [{location}]")


# =============================================================================
# Legacy scrape command (for backwards compatibility)
# =============================================================================

def legacy_scrape(args, config):
    """Legacy scrape command for one-time scraping"""
    scraper, site = get_scraper_for_url(args.url, config)
    if not scraper:
        print(f"Error: Unsupported site: {args.url}")
        return

    slug = extract_slug_from_url(args.url, site)
    if not slug:
        print("Error: Could not extract novel slug from URL")
        return

    print("=" * 60)
    print("Novel Scraper for Obsidian")
    print(f"Site: {site}")
    print(f"Novel: {args.name}")
    print(f"Slug: {slug}")
    print(f"Chapters: {args.start} to {args.end}")
    print(f"Output: {config.staging_dir}")
    print("=" * 60)

    # Get chapter list
    chapters = scraper.get_chapter_list(slug)
    if not chapters:
        print("Failed to fetch chapter list!")
        return

    print(f"Found {len(chapters)} chapters")

    # Filter by range
    chapters = [c for c in chapters if args.start <= c['num'] <= args.end]

    if not chapters:
        print("No chapters in the specified range!")
        return

    print(f"Scraping {len(chapters)} chapters\n")

    successful = 0
    failed = 0
    chapter_nums = []

    for i, chapter in enumerate(chapters):
        print(f"[{i+1}/{len(chapters)}] Chapter {chapter['num']}...", end=" ", flush=True)

        title, content = scraper.scrape_chapter_by_url(chapter['url'])

        if content:
            scraper.save_chapter(args.name, chapter['num'], title, content)
            chapter_nums.append(chapter['num'])
            print(f"OK ({len(content)} chars)")
            successful += 1
        else:
            print("FAILED")
            failed += 1

        if i < len(chapters) - 1:
            time.sleep(args.delay)

    if successful > 0:
        scraper.create_index_file(args.name, chapter_nums)

    print(f"\n{'='*60}")
    print(f"Complete: {successful} successful, {failed} failed")
    print(f"Output folder: {scraper.get_folder_name(args.name)}")
    print("=" * 60)


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Novel Scraper with DuckDB tracking and Obsidian integration',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Add a novel to track
  %(prog)s add --url "https://lightnovelstranslations.com/novel/..." --name "Novel Name"

  # Check for new chapters
  %(prog)s check

  # Download new chapters
  %(prog)s sync --all

  # Move to Obsidian
  %(prog)s move --all

  # Legacy one-time scrape
  %(prog)s scrape --url "..." --name "..." --start 1 --end 100
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Add command
    add_parser = subparsers.add_parser('add', help='Add a novel to track')
    add_parser.add_argument('--url', '-u', required=True, help='Novel URL')
    add_parser.add_argument('--name', '-n', required=True, help='Novel name')

    # List command
    list_parser = subparsers.add_parser('list', help='List tracked novels')
    list_parser.add_argument('--json', action='store_true', help='Output as JSON')

    # Remove command
    remove_parser = subparsers.add_parser('remove', help='Remove a novel')
    remove_parser.add_argument('name', help='Novel name')

    # Set status command
    status_parser = subparsers.add_parser('set-status', help='Set novel status')
    status_parser.add_argument('name', help='Novel name')
    status_parser.add_argument('status', choices=['ongoing', 'completed', 'hiatus', 'dropped'],
                               help='Novel status')

    # Check command
    check_parser = subparsers.add_parser('check', help='Check for new chapters')
    check_parser.add_argument('--name', '-n', help='Novel name (or check all)')
    check_parser.add_argument('--json', action='store_true', help='Output as JSON')

    # Sync command
    sync_parser = subparsers.add_parser('sync', help='Download new chapters')
    sync_parser.add_argument('--name', '-n', help='Novel name')
    sync_parser.add_argument('--all', action='store_true', help='Sync all novels')
    sync_parser.add_argument('--sequential', action='store_true', help='Download sequentially (not parallel)')

    # Move command
    move_parser = subparsers.add_parser('move', help='Move chapters to Obsidian')
    move_parser.add_argument('--name', '-n', help='Novel name')
    move_parser.add_argument('--all', action='store_true', help='Move all novels')

    # Scan Obsidian command
    scan_parser = subparsers.add_parser('scan-obsidian', help='Import existing novels from Obsidian')

    # Nav update command
    nav_parser = subparsers.add_parser('nav-update', help='Rebuild prev/next/index nav on chapter files')
    nav_parser.add_argument('--name', '-n', help='Novel name')
    nav_parser.add_argument('--all', action='store_true', help='Update all novels')
    nav_parser.add_argument('--vault', action='store_true', help='Target Obsidian vault (default: staging)')

    # Config command
    config_parser = subparsers.add_parser('config', help='View/set configuration')
    config_parser.add_argument('action', choices=['show', 'set'], help='Action')
    config_parser.add_argument('key', nargs='?', help='Config key (section.key)')
    config_parser.add_argument('value', nargs='?', help='Value to set')

    # Legacy scrape command
    scrape_parser = subparsers.add_parser('scrape', help='Legacy: one-time scrape')
    scrape_parser.add_argument('--url', '-u', required=True, help='Novel URL')
    scrape_parser.add_argument('--name', '-n', required=True, help='Novel name')
    scrape_parser.add_argument('--start', '-s', type=int, default=1, help='Start chapter')
    scrape_parser.add_argument('--end', '-e', type=int, required=True, help='End chapter')
    scrape_parser.add_argument('--delay', '-d', type=float, default=1.5, help='Delay between requests')

    args = parser.parse_args()

    # Load config
    config = Config()
    config.load()

    # Handle commands
    if args.command == 'add':
        manager = NovelManager(config)
        manager.add_novel(args.url, args.name)

    elif args.command == 'list':
        manager = NovelManager(config)
        manager.list_novels(json_output=args.json)

    elif args.command == 'remove':
        manager = NovelManager(config)
        manager.remove_novel(args.name)

    elif args.command == 'set-status':
        manager = NovelManager(config)
        with manager.db:
            novel = manager.db.get_novel(name=args.name)
            if not novel:
                print(f"Error: Novel '{args.name}' not found")
            else:
                manager.db.update_novel(novel['id'], status=args.status)
                print(f"Set '{args.name}' status to: {args.status}")

    elif args.command == 'check':
        manager = NovelManager(config)
        manager.check_novels(name=args.name, json_output=args.json)

    elif args.command == 'sync':
        manager = NovelManager(config)
        manager.sync_novel(name=args.name, all_novels=args.all, parallel=not args.sequential)

    elif args.command == 'move':
        manager = NovelManager(config)
        manager.move_to_obsidian(name=args.name, all_novels=args.all)

    elif args.command == 'scan-obsidian':
        manager = NovelManager(config)
        manager.scan_obsidian()

    elif args.command == 'nav-update':
        manager = NovelManager(config)
        manager.nav_update(name=args.name, all_novels=args.all, vault=args.vault)

    elif args.command == 'config':
        if args.action == 'show':
            print(f"Config file: {config.config_file}")
            print(f"Local config: {config.local_config_file}")
            print()
            print(f"staging_dir: {config.staging_dir}")
            print(f"obsidian_vault: {config.obsidian_vault or '(not set)'}")
            print(f"database: {config.database_path}")
            print(f"delay: {config.delay}")
            print(f"max_workers: {config.max_workers}")
        elif args.action == 'set':
            if not args.key or not args.value:
                print("Usage: config set <key> <value>")
                print("Example: config set obsidian_vault /path/to/vault")
                return
            # Parse key as section.key or just key (defaults to paths section)
            if '.' in args.key:
                section, key = args.key.split('.', 1)
            else:
                section = 'paths'
                key = args.key
            config.set(section, key, args.value)
            print(f"Set {section}.{key} = {args.value}")

    elif args.command == 'scrape':
        legacy_scrape(args, config)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
