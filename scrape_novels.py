#!/usr/bin/env python3
"""
Multi-site Novel Scraper using cloudscraper
Supports: novelbin.me/novelbin.com (both simple and title-based URLs)
          webnovel.com (using Selenium for JavaScript rendering)
          lightnovelstranslations.com
          freewebnovel.com
Outputs Obsidian-compatible markdown with tags and index
"""

import os
import re
import time
import cloudscraper
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


class NovelScraper:
    def __init__(self, output_dir="novels"):
        self.output_dir = output_dir
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
        self.chapters_saved = []

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
        """Get wikilink format: 0001_-_Novel_Name"""
        folder_name = self.get_folder_name(novel_name)
        return f"{chapter_num:04d}_-_{folder_name.replace(' ', '_')}"

    def save_chapter(self, novel_name, chapter_num, title, content):
        """Save chapter in Obsidian format"""
        folder_name = self.get_folder_name(novel_name)
        novel_dir = os.path.join(self.output_dir, folder_name)
        os.makedirs(novel_dir, exist_ok=True)

        filename = self.get_chapter_filename(novel_name, chapter_num)
        filepath = os.path.join(novel_dir, filename)

        tag_slug = self.to_kebab_case(novel_name)

        # Obsidian format with YAML frontmatter
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

        self.chapters_saved.append(chapter_num)
        return filepath

    def create_index_file(self, novel_name, total_chapters=None):
        """Create index file with links to all chapters"""
        folder_name = self.get_folder_name(novel_name)
        novel_dir = os.path.join(self.output_dir, folder_name)

        if not os.path.exists(novel_dir):
            return None

        tag_slug = self.to_kebab_case(novel_name)
        index_filename = f"{folder_name.replace(' ', '_')}_Index.md"
        index_filepath = os.path.join(novel_dir, index_filename)

        # Get list of chapter numbers from saved chapters or scan directory
        if self.chapters_saved:
            chapter_nums = sorted(self.chapters_saved)
        else:
            chapter_nums = []
            for f in os.listdir(novel_dir):
                if f.endswith('.md') and f[0].isdigit():
                    try:
                        num = int(f.split(' - ')[0])
                        chapter_nums.append(num)
                    except (ValueError, IndexError):
                        continue
            chapter_nums.sort()

        # Build Table of Contents
        toc_lines = []
        for num in chapter_nums:
            wikilink = self.get_wikilink_name(novel_name, num)
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

        print(f"Created index file: {index_filename}")
        return index_filepath


class NovelBinScraper(NovelScraper):
    """Scraper for novelbin.me / novelbin.com"""

    def __init__(self, output_dir="novels"):
        super().__init__(output_dir)
        self.base_url = "https://novelbin.com"

    def get_chapter_list(self, novel_slug):
        """Fetch all chapter URLs from AJAX endpoint"""
        ajax_url = f"{self.base_url}/ajax/chapter-archive?novelId={novel_slug}"

        try:
            response = self.scraper.get(ajax_url, timeout=30)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                links = soup.select('a[href*="chapter"]')

                chapters = []
                for link in links:
                    href = link.get('href', '')
                    text = link.get_text(strip=True)

                    # Extract chapter number from text
                    match = re.search(r'Chapter\s+(\d+)', text, re.IGNORECASE)
                    if match:
                        chapter_num = int(match.group(1))
                        chapters.append((chapter_num, href, text))

                # Sort by chapter number
                chapters.sort(key=lambda x: x[0])
                return chapters
        except Exception as e:
            print(f"Error fetching chapter list: {e}")

        return []

    def get_chapter_url(self, novel_slug, chapter_num):
        """Generate simple chapter URL (fallback)"""
        return f"{self.base_url}/b/{novel_slug}/chapter-{chapter_num}"

    def scrape_chapter_by_url(self, url, retries=3):
        """Scrape a single chapter by full URL"""
        for attempt in range(retries):
            try:
                response = self.scraper.get(url, timeout=30)

                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'html.parser')

                    # Get title
                    title = "Chapter"
                    title_selectors = [
                        ".chr-title",
                        "h2 a.chr-title",
                        ".chapter-title",
                        "h2.title",
                        "h1",
                    ]
                    for selector in title_selectors:
                        title_elem = soup.select_one(selector)
                        if title_elem and title_elem.get_text(strip=True):
                            title = title_elem.get_text(strip=True)
                            break

                    # Get content
                    content_selectors = [
                        "#chr-content",
                        ".chr-c",
                        "#chapter-content",
                        ".chapter-content",
                        ".reading-content",
                    ]

                    content = ""
                    for selector in content_selectors:
                        content_elem = soup.select_one(selector)
                        if content_elem:
                            paragraphs = content_elem.find_all('p')
                            if paragraphs:
                                content_parts = []
                                for p in paragraphs:
                                    text = p.get_text(strip=True)
                                    if len(text) > 15:
                                        skip_phrases = ['prev', 'next', 'comment', 'report', 'login', 'novelbin', 'bookmark']
                                        if not any(skip in text.lower()[:50] for skip in skip_phrases):
                                            content_parts.append(text)
                                if content_parts:
                                    content = '\n\n'.join(content_parts)
                                    break

                    # Fallback
                    if not content or len(content) < 100:
                        paragraphs = soup.find_all('p')
                        content_parts = []
                        for p in paragraphs:
                            text = p.get_text(strip=True)
                            if len(text) > 30:
                                skip_phrases = ['prev', 'next', 'comment', 'report', 'login', 'novelbin', 'bookmark', 'chapter list']
                                if not any(skip in text.lower()[:50] for skip in skip_phrases):
                                    content_parts.append(text)
                        if content_parts:
                            content = '\n\n'.join(content_parts)

                    if content and len(content) > 50:
                        return title, content
                    else:
                        if attempt < retries - 1:
                            print(f"  (attempt {attempt+1}) Low content: {len(content) if content else 0} chars")
                elif response.status_code == 404:
                    return None, None
                else:
                    print(f"  (attempt {attempt+1}) HTTP {response.status_code}")

            except Exception as e:
                print(f"  (attempt {attempt+1}) Error: {str(e)[:50]}")

            if attempt < retries - 1:
                time.sleep(3)

        return None, None

    def scrape_chapter(self, novel_slug, chapter_num, retries=3):
        """Scrape a single chapter using simple URL"""
        url = self.get_chapter_url(novel_slug, chapter_num)
        return self.scrape_chapter_by_url(url, retries)

    def scrape_with_chapter_list(self, novel_slug, novel_name, start_chapter=1, end_chapter=None, delay=2):
        """Scrape using chapter list (for title-based URLs)"""
        folder_name = self.get_folder_name(novel_name)

        print(f"\n{'='*60}")
        print(f"Fetching chapter list for: {folder_name}")
        print(f"{'='*60}")

        chapters = self.get_chapter_list(novel_slug)

        if not chapters:
            print("Failed to fetch chapter list!")
            return 0, 0

        print(f"Found {len(chapters)} chapters")

        # Filter by range
        if end_chapter:
            chapters = [(num, url, title) for num, url, title in chapters if start_chapter <= num <= end_chapter]
        else:
            chapters = [(num, url, title) for num, url, title in chapters if num >= start_chapter]

        print(f"Scraping {len(chapters)} chapters ({start_chapter} to {chapters[-1][0] if chapters else 'N/A'})")
        print(f"{'='*60}\n")

        self.chapters_saved = []
        successful = 0
        failed = 0
        failed_chapters = []

        total = len(chapters)
        for i, (chapter_num, url, chapter_title) in enumerate(chapters):
            print(f"[{i+1}/{total}] Chapter {chapter_num}...", end=" ", flush=True)

            title, content = self.scrape_chapter_by_url(url)

            if content:
                filepath = self.save_chapter(novel_name, chapter_num, title, content)
                print(f"OK ({len(content)} chars)")
                successful += 1
            else:
                print("FAILED")
                failed += 1
                failed_chapters.append(chapter_num)

            if i < total - 1:
                time.sleep(delay)

        # Create index file
        if successful > 0:
            self.create_index_file(novel_name)

        print(f"\n{'='*60}")
        print(f"Complete: {successful} successful, {failed} failed")
        print(f"Output folder: {folder_name}")
        if failed_chapters:
            print(f"Failed chapters: {failed_chapters[:20]}{'...' if len(failed_chapters) > 20 else ''}")
        print(f"{'='*60}")

        return successful, failed

    def scrape_range(self, novel_slug, novel_name, start_chapter, end_chapter, delay=2):
        """Scrape a range of chapters (simple URL format)"""
        folder_name = self.get_folder_name(novel_name)

        print(f"\n{'='*60}")
        print(f"Scraping: {folder_name}")
        print(f"Source: novelbin.com")
        print(f"Chapters: {start_chapter} to {end_chapter}")
        print(f"{'='*60}\n")

        self.chapters_saved = []
        successful = 0
        failed = 0
        failed_chapters = []

        for chapter_num in range(start_chapter, end_chapter + 1):
            print(f"[{chapter_num}/{end_chapter}] Scraping chapter {chapter_num}...", end=" ", flush=True)

            title, content = self.scrape_chapter(novel_slug, chapter_num)

            if content:
                filepath = self.save_chapter(novel_name, chapter_num, title, content)
                print(f"OK ({len(content)} chars)")
                successful += 1
            else:
                print("FAILED")
                failed += 1
                failed_chapters.append(chapter_num)

            if chapter_num < end_chapter:
                time.sleep(delay)

        # Create index file
        if successful > 0:
            self.create_index_file(novel_name)

        print(f"\n{'='*60}")
        print(f"Complete: {successful} successful, {failed} failed")
        print(f"Output folder: {folder_name}")
        if failed_chapters:
            print(f"Failed chapters: {failed_chapters[:20]}{'...' if len(failed_chapters) > 20 else ''}")
        print(f"{'='*60}")

        return successful, failed


class FreeWebNovelScraper(NovelScraper):
    """Scraper for freewebnovel.com - no Cloudflare issues"""

    def __init__(self, output_dir="novels"):
        super().__init__(output_dir)
        self.base_url = "https://freewebnovel.com"

    def get_chapter_url(self, novel_slug, chapter_num):
        """Generate chapter URL"""
        return f"{self.base_url}/novel/{novel_slug}/chapter-{chapter_num}"

    def scrape_chapter(self, novel_slug, chapter_num, retries=3):
        """Scrape a single chapter"""
        url = self.get_chapter_url(novel_slug, chapter_num)

        for attempt in range(retries):
            try:
                response = self.scraper.get(url, timeout=30)

                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'html.parser')

                    # Get title
                    title = f"Chapter {chapter_num}"
                    title_elem = soup.select_one('h1.tit, .chapter-title, h1')
                    if title_elem:
                        title = title_elem.get_text(strip=True)

                    # Get content - freewebnovel uses paragraphs directly
                    content_parts = []
                    paragraphs = soup.find_all('p')

                    for p in paragraphs:
                        text = p.get_text(strip=True)
                        if len(text) > 15:
                            skip_phrases = [
                                'prev chapter', 'next chapter', 'use arrow keys',
                                'report chapter', 'freewebnovel.com', 'contact',
                                'sitemap', 'privacy policy', 'log in', 'create account',
                                'tap the screen', 'add to library', 'submit', 'comments',
                                'welcome to freewebnovel', "don't have an account"
                            ]
                            if not any(skip in text.lower() for skip in skip_phrases):
                                content_parts.append(text)

                    if content_parts:
                        content = '\n\n'.join(content_parts)
                        if len(content) > 100:
                            return title, content

                elif response.status_code == 404:
                    return None, None

            except Exception as e:
                if attempt < retries - 1:
                    print(f"  (attempt {attempt+1}) Error: {str(e)[:50]}")

            if attempt < retries - 1:
                time.sleep(2)

        return None, None

    def scrape_range(self, novel_slug, novel_name, start_chapter, end_chapter, delay=1.5):
        """Scrape a range of chapters"""
        folder_name = self.get_folder_name(novel_name)

        print(f"\n{'='*60}")
        print(f"Scraping: {folder_name}")
        print(f"Source: freewebnovel.com")
        print(f"Chapters: {start_chapter} to {end_chapter}")
        print(f"{'='*60}\n")

        self.chapters_saved = []
        successful = 0
        failed = 0
        failed_chapters = []

        for chapter_num in range(start_chapter, end_chapter + 1):
            print(f"[{chapter_num}/{end_chapter}] Chapter {chapter_num}...", end=" ", flush=True)

            title, content = self.scrape_chapter(novel_slug, chapter_num)

            if content:
                self.save_chapter(novel_name, chapter_num, title, content)
                print(f"OK ({len(content)} chars)")
                successful += 1
            else:
                print("FAILED")
                failed += 1
                failed_chapters.append(chapter_num)

            if chapter_num < end_chapter:
                time.sleep(delay)

        # Create index file
        if successful > 0:
            self.create_index_file(novel_name)

        print(f"\n{'='*60}")
        print(f"Complete: {successful} successful, {failed} failed")
        print(f"Output folder: {folder_name}")
        if failed_chapters:
            print(f"Failed chapters: {failed_chapters[:20]}{'...' if len(failed_chapters) > 20 else ''}")
        print(f"{'='*60}")

        return successful, failed


class LightNovelTranslationsScraper(NovelScraper):
    """Scraper for lightnovelstranslations.com"""

    def __init__(self, output_dir="novels"):
        super().__init__(output_dir)
        self.base_url = "https://lightnovelstranslations.com"

    def get_chapter_list(self, novel_slug):
        """Fetch all chapter URLs from the table of contents page"""
        toc_url = f"{self.base_url}/novel/{novel_slug}/?tab=table_contents"

        try:
            response = self.scraper.get(toc_url, timeout=30)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')

                # Find all chapter links
                chapters = []
                links = soup.find_all('a', href=True)

                for link in links:
                    href = link.get('href', '')
                    # Match chapter URLs for this novel
                    if f'/novel/{novel_slug}/' in href and href != toc_url:
                        text = link.get_text(strip=True)
                        title = link.get('title', text)

                        # Skip navigation links and non-chapter links
                        if not text or 'tab=' in href:
                            continue

                        # Extract chapter number from text or URL
                        chapter_num = None

                        # Try to find chapter number in text
                        match = re.search(r'Chapter\s*(\d+)', text, re.IGNORECASE)
                        if match:
                            chapter_num = int(match.group(1))
                        else:
                            # Try URL pattern
                            url_match = re.search(r'chapter-?(\d+)', href, re.IGNORECASE)
                            if url_match:
                                chapter_num = int(url_match.group(1))

                        if chapter_num is not None:
                            chapters.append({
                                'num': chapter_num,
                                'url': href,
                                'title': title or text
                            })

                # Remove duplicates (keep first occurrence)
                seen = set()
                unique_chapters = []
                for ch in chapters:
                    if ch['num'] not in seen:
                        seen.add(ch['num'])
                        unique_chapters.append(ch)

                # Sort by chapter number
                unique_chapters.sort(key=lambda x: x['num'])
                return unique_chapters

        except Exception as e:
            print(f"Error fetching chapter list: {e}")

        return []

    def scrape_chapter_by_url(self, url, retries=3):
        """Scrape a single chapter by URL"""
        for attempt in range(retries):
            try:
                response = self.scraper.get(url, timeout=30)

                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'html.parser')

                    # Get chapter title
                    title = "Chapter"
                    title_selectors = [
                        "h2",
                        "h1.entry-title",
                        ".entry-title",
                        "h1",
                        ".chapter-title"
                    ]
                    for selector in title_selectors:
                        title_elem = soup.select_one(selector)
                        if title_elem and title_elem.get_text(strip=True):
                            title = title_elem.get_text(strip=True)
                            break

                    # Get content - try various selectors
                    content_selectors = [
                        ".entry-content",
                        ".post-content",
                        ".chapter-content",
                        ".reading-content",
                        "article .content",
                        ".text-left",
                        "article"
                    ]

                    content = ""
                    for selector in content_selectors:
                        content_elem = soup.select_one(selector)
                        if content_elem:
                            paragraphs = content_elem.find_all('p')
                            if paragraphs:
                                content_parts = []
                                for p in paragraphs:
                                    text = p.get_text(strip=True)
                                    if len(text) > 15:
                                        # Skip navigation, ads, and meta content
                                        skip_phrases = [
                                            'prev chapter', 'next chapter', 'previous chapter',
                                            'translator:', 'editor:', 'proofreader:',
                                            'support us', 'patreon', 'kofi', 'ko-fi',
                                            'adsbygoogle', 'advertisement', 'sponsored',
                                            'bookmark', 'comment', 'report chapter',
                                            'font size', 'background color', 'reading mode',
                                            'previous_page', 'next_page', 'login', 'register',
                                            'vip member', 'table of contents', 'chapter list'
                                        ]
                                        text_lower = text.lower()
                                        if not any(skip in text_lower for skip in skip_phrases):
                                            content_parts.append(text)

                                if content_parts:
                                    content = '\n\n'.join(content_parts)
                                    break

                    # Fallback: get all paragraphs from page
                    if not content or len(content) < 100:
                        paragraphs = soup.find_all('p')
                        content_parts = []
                        for p in paragraphs:
                            text = p.get_text(strip=True)
                            if len(text) > 30:
                                skip_phrases = [
                                    'prev chapter', 'next chapter', 'previous chapter',
                                    'translator:', 'editor:', 'support us', 'patreon',
                                    'adsbygoogle', 'bookmark', 'comment', 'report',
                                    'font size', 'login', 'register', 'vip member',
                                    'table of contents', 'chapter list', 'previous_page',
                                    'next_page', 'reading mode', 'sponsored'
                                ]
                                text_lower = text.lower()
                                if not any(skip in text_lower for skip in skip_phrases):
                                    content_parts.append(text)

                        if content_parts:
                            content = '\n\n'.join(content_parts)

                    if content and len(content) > 50:
                        return title, content
                    else:
                        if attempt < retries - 1:
                            print(f"  (attempt {attempt+1}) Low content: {len(content) if content else 0} chars")

                elif response.status_code == 404:
                    return None, None
                else:
                    print(f"  (attempt {attempt+1}) HTTP {response.status_code}")

            except Exception as e:
                if attempt < retries - 1:
                    print(f"  (attempt {attempt+1}) Error: {str(e)[:50]}")

            if attempt < retries - 1:
                time.sleep(2)

        return None, None

    def scrape_range(self, novel_slug, novel_name, start_chapter=1, end_chapter=None, delay=1.5):
        """Scrape chapters using the chapter list"""
        folder_name = self.get_folder_name(novel_name)

        print(f"\n{'='*60}")
        print(f"Fetching chapter list for: {folder_name}")
        print(f"Source: lightnovelstranslations.com")
        print(f"{'='*60}")

        chapters = self.get_chapter_list(novel_slug)

        if not chapters:
            print("Failed to fetch chapter list!")
            return 0, 0

        print(f"Found {len(chapters)} chapters")

        # Filter by range
        if end_chapter:
            chapters = [c for c in chapters if start_chapter <= c['num'] <= end_chapter]
        else:
            chapters = [c for c in chapters if c['num'] >= start_chapter]

        if not chapters:
            print("No chapters found in the specified range!")
            return 0, 0

        print(f"Scraping {len(chapters)} chapters ({chapters[0]['num']} to {chapters[-1]['num']})")
        print(f"{'='*60}\n")

        self.chapters_saved = []
        successful = 0
        failed = 0
        failed_chapters = []

        total = len(chapters)
        for i, chapter in enumerate(chapters):
            chapter_num = chapter['num']
            url = chapter['url']
            print(f"[{i+1}/{total}] Chapter {chapter_num}...", end=" ", flush=True)

            title, content = self.scrape_chapter_by_url(url)

            if content:
                self.save_chapter(novel_name, chapter_num, title, content)
                print(f"OK ({len(content)} chars)")
                successful += 1
            else:
                print("FAILED")
                failed += 1
                failed_chapters.append(chapter_num)

            if i < total - 1:
                time.sleep(delay)

        # Create index file
        if successful > 0:
            self.create_index_file(novel_name)

        print(f"\n{'='*60}")
        print(f"Complete: {successful} successful, {failed} failed")
        print(f"Output folder: {folder_name}")
        if failed_chapters:
            print(f"Failed chapters: {failed_chapters[:20]}{'...' if len(failed_chapters) > 20 else ''}")
        print(f"{'='*60}")

        return successful, failed


class WebNovelScraper(NovelScraper):
    """Scraper for webnovel.com - uses API and cloudscraper"""

    def __init__(self, output_dir="novels", headless=True):
        super().__init__(output_dir)
        self.base_url = "https://www.webnovel.com"
        self.mobile_url = "https://m.webnovel.com"
        self.api_url = "https://www.webnovel.com/go/pcm/chapter"
        self.headless = headless
        self.use_selenium = False
        self.driver = None

        # Update headers for webnovel.com
        self.scraper.headers.update({
            'Accept': 'application/json, text/plain, */*',
            'Referer': 'https://www.webnovel.com/',
            'Origin': 'https://www.webnovel.com',
        })

    def _init_driver(self):
        """Initialize Selenium WebDriver (fallback)"""
        if self.driver is not None:
            return

        try:
            chrome_options = Options()
            if self.headless:
                chrome_options.add_argument("--headless=new")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            chrome_options.add_argument("--disable-blink-features=AutomationControlled")
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)

            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
            })
            self.use_selenium = True
        except Exception as e:
            print(f"Could not initialize Selenium: {e}")
            print("Continuing with cloudscraper only...")
            self.use_selenium = False

    def _close_driver(self):
        """Close the WebDriver"""
        if self.driver:
            self.driver.quit()
            self.driver = None

    def get_novel_info(self, book_id):
        """Get novel title and chapter list from the book page"""
        url = f"{self.base_url}/book/{book_id}"
        print(f"Fetching novel info from: {url}")

        try:
            response = self.scraper.get(url, timeout=30)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')

                # Get novel title
                title = "Unknown Novel"
                title_selectors = [
                    "h1.pt4.pb4.ell.dib",
                    "h1",
                    ".g_title",
                    ".det-hd h1",
                    "meta[property='og:title']"
                ]
                for selector in title_selectors:
                    if selector.startswith('meta'):
                        title_elem = soup.select_one(selector)
                        if title_elem:
                            title = title_elem.get('content', '')
                            if title:
                                break
                    else:
                        title_elem = soup.select_one(selector)
                        if title_elem and title_elem.get_text(strip=True):
                            title = title_elem.get_text(strip=True)
                            break

                # Try to get chapter list from various sources
                chapters = []

                # Method 1: Look for chapter links in page
                chapter_selectors = [
                    "a[href*='/book/'][href*='/']",
                    ".volume-list a",
                    ".catalog-wrap a",
                    ".chapter-item a"
                ]

                for selector in chapter_selectors:
                    links = soup.select(selector)
                    for link in links:
                        href = link.get('href', '')
                        text = link.get_text(strip=True)
                        if '/book/' in href and book_id in href:
                            parts = href.rstrip('/').split('/')
                            if len(parts) >= 2:
                                chapter_id = parts[-1]
                                if chapter_id.isdigit() and chapter_id != book_id:
                                    match = re.search(r'Chapter\s*(\d+)', text, re.IGNORECASE)
                                    chapter_num = int(match.group(1)) if match else len(chapters) + 1
                                    chapters.append({
                                        'num': chapter_num,
                                        'id': chapter_id,
                                        'title': text,
                                        'url': f"{self.base_url}{href}" if href.startswith('/') else href
                                    })

                # Method 2: Try the API endpoint for chapter list
                if not chapters:
                    api_url = f"{self.base_url}/go/pcm/bookIndex/getBookIndexList"
                    params = {
                        '_csrfToken': '',
                        'bookId': book_id,
                    }
                    try:
                        api_response = self.scraper.get(api_url, params=params, timeout=30)
                        if api_response.status_code == 200:
                            data = api_response.json()
                            if data.get('code') == 0 and 'data' in data:
                                volumes = data['data'].get('volumeItems', [])
                                for volume in volumes:
                                    for chapter in volume.get('chapterItems', []):
                                        chapters.append({
                                            'num': chapter.get('index', len(chapters) + 1),
                                            'id': str(chapter.get('id', '')),
                                            'title': chapter.get('name', f"Chapter {len(chapters) + 1}"),
                                            'url': f"{self.base_url}/book/{book_id}/{chapter.get('id', '')}"
                                        })
                    except Exception as e:
                        print(f"API chapter list failed: {e}")

                # Remove duplicates
                seen = set()
                unique_chapters = []
                for ch in chapters:
                    if ch['id'] not in seen:
                        seen.add(ch['id'])
                        unique_chapters.append(ch)

                # Sort by chapter number
                unique_chapters.sort(key=lambda x: x['num'])

                return title, unique_chapters

        except Exception as e:
            print(f"Error fetching novel info: {e}")

        return None, []

    def get_chapter_url(self, book_id, chapter_id):
        """Generate chapter URL"""
        return f"{self.base_url}/book/{book_id}/{chapter_id}"

    def scrape_chapter_by_url(self, url, retries=3):
        """Scrape a single chapter by URL"""
        for attempt in range(retries):
            try:
                response = self.scraper.get(url, timeout=30)

                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'html.parser')

                    # Get chapter title
                    title = "Chapter"
                    title_selectors = [
                        ".cha-tit .dib.ell",
                        ".cha-tit h1",
                        ".chapter-title",
                        ".cha-hd-txt",
                        "h1.chapter-tit",
                        ".tit",
                        "h1"
                    ]
                    for selector in title_selectors:
                        title_elem = soup.select_one(selector)
                        if title_elem and title_elem.get_text(strip=True):
                            title = title_elem.get_text(strip=True)
                            break

                    # Get content
                    content_selectors = [
                        ".cha-content .cha-words",
                        ".chapter-content",
                        ".cha-content",
                        ".chapter_content",
                        ".j_contentBox",
                        ".content-container",
                        ".cha-words",
                        ".chapter-body"
                    ]

                    content = ""
                    for selector in content_selectors:
                        content_elem = soup.select_one(selector)
                        if content_elem:
                            # Get paragraphs
                            paragraphs = content_elem.find_all('p')
                            if paragraphs:
                                content_parts = []
                                for p in paragraphs:
                                    text = p.get_text(strip=True)
                                    if len(text) > 10:
                                        skip_phrases = [
                                            'translator', 'editor:', 'proofreader',
                                            'support us', 'webnovel.com', 'unlock',
                                            'subscribe', 'comment', 'vote', 'coin',
                                            'prev chapter', 'next chapter', 'report',
                                            'please use the app', 'support the creator',
                                            'reading preference', 'font', 'webnovel'
                                        ]
                                        if not any(skip in text.lower()[:80] for skip in skip_phrases):
                                            content_parts.append(text)
                                if content_parts:
                                    content = '\n\n'.join(content_parts)
                                    break

                            # Fallback: get direct text
                            if not content:
                                text = content_elem.get_text(separator='\n', strip=True)
                                if len(text) > 100:
                                    # Clean up the text
                                    lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip()) > 10]
                                    content = '\n\n'.join(lines)

                    if content and len(content) > 50:
                        return title, content
                    else:
                        if attempt < retries - 1:
                            print(f"  (attempt {attempt+1}) Low content: {len(content) if content else 0} chars")

                elif response.status_code == 403:
                    print(f"  (attempt {attempt+1}) Access denied (403)")
                elif response.status_code == 404:
                    return None, None
                else:
                    print(f"  (attempt {attempt+1}) HTTP {response.status_code}")

            except Exception as e:
                if attempt < retries - 1:
                    print(f"  (attempt {attempt+1}) Error: {str(e)[:50]}")

            if attempt < retries - 1:
                time.sleep(3)

        return None, None

    def scrape_chapter_api(self, book_id, chapter_id, retries=3):
        """Try to get chapter content via API"""
        api_url = f"{self.base_url}/go/pcm/chapter/getContent"
        params = {
            '_csrfToken': '',
            'bookId': book_id,
            'chapterId': chapter_id,
        }

        for attempt in range(retries):
            try:
                response = self.scraper.get(api_url, params=params, timeout=30)
                if response.status_code == 200:
                    data = response.json()
                    if data.get('code') == 0 and 'data' in data:
                        chapter_data = data['data'].get('chapterInfo', {})
                        title = chapter_data.get('chapterName', 'Chapter')
                        content = chapter_data.get('content', '')

                        if content:
                            # Clean HTML from content
                            soup = BeautifulSoup(content, 'html.parser')
                            paragraphs = soup.find_all('p')
                            if paragraphs:
                                content = '\n\n'.join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
                            else:
                                content = soup.get_text(separator='\n\n', strip=True)

                            if len(content) > 50:
                                return title, content
            except Exception as e:
                if attempt < retries - 1:
                    print(f"  API (attempt {attempt+1}) Error: {str(e)[:50]}")

            if attempt < retries - 1:
                time.sleep(2)

        return None, None

    def scrape_chapter(self, book_id, chapter_id, retries=3):
        """Scrape a single chapter - tries API first, then URL"""
        # Try API first
        title, content = self.scrape_chapter_api(book_id, chapter_id, retries=1)
        if content:
            return title, content

        # Fallback to URL scraping
        url = self.get_chapter_url(book_id, chapter_id)
        return self.scrape_chapter_by_url(url, retries)

    def scrape_range(self, book_id, novel_name, start_chapter=1, end_chapter=None, delay=2):
        """Scrape a range of chapters"""
        folder_name = self.get_folder_name(novel_name)

        print(f"\n{'='*60}")
        print(f"Scraping: {folder_name}")
        print(f"Source: webnovel.com")
        print(f"Book ID: {book_id}")
        print(f"{'='*60}\n")

        # First get chapter list
        print("Fetching chapter list...")
        title, chapters = self.get_novel_info(book_id)

        if title and title != "Unknown Novel":
            print(f"Novel title: {title}")

        if not chapters:
            print("Could not fetch chapter list automatically.")
            print("Trying sequential chapter IDs...")
            return self._scrape_sequential(book_id, novel_name, start_chapter, end_chapter or 100, delay)

        print(f"Found {len(chapters)} chapters")

        # Filter by range
        if end_chapter:
            chapters = [c for c in chapters if start_chapter <= c['num'] <= end_chapter]
        else:
            chapters = [c for c in chapters if c['num'] >= start_chapter]

        if not chapters:
            print("No chapters found in the specified range!")
            return 0, 0

        print(f"Scraping {len(chapters)} chapters ({chapters[0]['num']} to {chapters[-1]['num']})")
        print(f"{'='*60}\n")

        self.chapters_saved = []
        successful = 0
        failed = 0
        failed_chapters = []

        total = len(chapters)
        for i, chapter in enumerate(chapters):
            chapter_num = chapter['num']
            chapter_id = chapter['id']
            print(f"[{i+1}/{total}] Chapter {chapter_num}...", end=" ", flush=True)

            title, content = self.scrape_chapter(book_id, chapter_id)

            if content:
                self.save_chapter(novel_name, chapter_num, title, content)
                print(f"OK ({len(content)} chars)")
                successful += 1
            else:
                print("FAILED")
                failed += 1
                failed_chapters.append(chapter_num)

            if i < total - 1:
                time.sleep(delay)

        # Create index file
        if successful > 0:
            self.create_index_file(novel_name)

        self._close_driver()

        print(f"\n{'='*60}")
        print(f"Complete: {successful} successful, {failed} failed")
        print(f"Output folder: {folder_name}")
        if failed_chapters:
            print(f"Failed chapters: {failed_chapters[:20]}{'...' if len(failed_chapters) > 20 else ''}")
        print(f"{'='*60}")

        return successful, failed

    def _scrape_sequential(self, book_id, novel_name, start_chapter, end_chapter, delay):
        """Fallback: scrape using sequential chapter IDs (often doesn't work)"""
        folder_name = self.get_folder_name(novel_name)

        print(f"Trying sequential scraping from chapter {start_chapter} to {end_chapter}")
        print("Note: This may not work as webnovel.com uses random chapter IDs.")
        print(f"{'='*60}\n")

        self.chapters_saved = []
        successful = 0
        failed = 0
        consecutive_failures = 0
        failed_chapters = []

        for chapter_num in range(start_chapter, end_chapter + 1):
            print(f"[{chapter_num}/{end_chapter}] Chapter {chapter_num}...", end=" ", flush=True)

            # For sequential, the chapter_id might be the same as chapter_num for some books
            title, content = self.scrape_chapter(book_id, str(chapter_num))

            if content:
                self.save_chapter(novel_name, chapter_num, title, content)
                print(f"OK ({len(content)} chars)")
                successful += 1
                consecutive_failures = 0
            else:
                print("FAILED")
                failed += 1
                failed_chapters.append(chapter_num)
                consecutive_failures += 1

                # Stop if too many consecutive failures
                if consecutive_failures >= 5:
                    print(f"\nStopping: {consecutive_failures} consecutive failures")
                    print("This book likely uses non-sequential chapter IDs.")
                    break

            if chapter_num < end_chapter:
                time.sleep(delay)

        # Create index file
        if successful > 0:
            self.create_index_file(novel_name)

        self._close_driver()

        print(f"\n{'='*60}")
        print(f"Complete: {successful} successful, {failed} failed")
        print(f"Output folder: {folder_name}")
        if failed_chapters:
            print(f"Failed chapters: {failed_chapters[:20]}{'...' if len(failed_chapters) > 20 else ''}")
        print(f"{'='*60}")

        return successful, failed


def main():
    """CLI interface for scraping novels"""
    import argparse

    parser = argparse.ArgumentParser(description='Novel Scraper for Obsidian')
    parser.add_argument('--url', '-u', required=True, help='Novel URL (freewebnovel.com, webnovel.com)')
    parser.add_argument('--name', '-n', required=True, help='Novel name for folder/tags')
    parser.add_argument('--start', '-s', type=int, default=1, help='Start chapter (default: 1)')
    parser.add_argument('--end', '-e', type=int, required=True, help='End chapter')
    parser.add_argument('--output', '-o', default='novels_obsidian', help='Output directory (default: novels_obsidian)')
    parser.add_argument('--delay', '-d', type=float, default=1.5, help='Delay between requests (default: 1.5)')
    parser.add_argument('--headless', action='store_true', default=True, help='Run browser in headless mode (default: True)')
    parser.add_argument('--no-headless', action='store_false', dest='headless', help='Show browser window')

    args = parser.parse_args()

    # Detect which site and extract identifier
    url = args.url.lower()

    if 'lightnovelstranslations.com' in url:
        # lightnovelstranslations.com URL patterns:
        # https://lightnovelstranslations.com/novel/novel-slug/
        # https://lightnovelstranslations.com/novel/novel-slug/?tab=table_contents

        # Extract novel slug from URL
        match = re.search(r'/novel/([^/?]+)', args.url)
        if match:
            novel_slug = match.group(1)
        else:
            print("Error: Could not extract novel slug from lightnovelstranslations.com URL")
            return

        print("="*60)
        print("Novel Scraper for Obsidian")
        print(f"Site: lightnovelstranslations.com")
        print(f"Novel: {args.name}")
        print(f"Slug: {novel_slug}")
        print(f"Chapters: {args.start} to {args.end}")
        print(f"Output: {args.output}")
        print("="*60)

        scraper = LightNovelTranslationsScraper(output_dir=args.output)
        scraper.scrape_range(
            novel_slug=novel_slug,
            novel_name=args.name,
            start_chapter=args.start,
            end_chapter=args.end,
            delay=args.delay
        )

    elif 'webnovel.com' in url:
        # webnovel.com URL patterns:
        # https://www.webnovel.com/book/345219114701937
        # https://m.webnovel.com/subject/345219114701937
        # https://m.webnovel.com/book/345219114701937

        # Extract book ID (numeric ID from URL)
        match = re.search(r'(?:book|subject)/(\d+)', args.url)
        if match:
            book_id = match.group(1)
        else:
            # Try to get last numeric segment
            parts = args.url.rstrip('/').split('/')
            book_id = None
            for part in reversed(parts):
                if part.isdigit():
                    book_id = part
                    break
            if not book_id:
                print("Error: Could not extract book ID from webnovel.com URL")
                return

        print("="*60)
        print("Novel Scraper for Obsidian")
        print(f"Site: webnovel.com")
        print(f"Novel: {args.name}")
        print(f"Book ID: {book_id}")
        print(f"Chapters: {args.start} to {args.end}")
        print(f"Output: {args.output}")
        print(f"Headless: {args.headless}")
        print("="*60)

        scraper = WebNovelScraper(output_dir=args.output, headless=args.headless)
        scraper.scrape_range(
            book_id=book_id,
            novel_name=args.name,
            start_chapter=args.start,
            end_chapter=args.end,
            delay=args.delay
        )

    else:
        # freewebnovel.com (default)
        # Extract slug from URL
        # Supports: https://freewebnovel.com/novel-name.html or https://freewebnovel.com/novel/novel-name/...
        slug = args.url.rstrip('/').split('/')[-1].replace('.html', '')
        if slug.startswith('chapter-'):
            slug = args.url.rstrip('/').split('/')[-2]

        print("="*60)
        print("Novel Scraper for Obsidian")
        print(f"Site: freewebnovel.com")
        print(f"Novel: {args.name}")
        print(f"Slug: {slug}")
        print(f"Chapters: {args.start} to {args.end}")
        print(f"Output: {args.output}")
        print("="*60)

        scraper = FreeWebNovelScraper(output_dir=args.output)
        scraper.scrape_range(
            novel_slug=slug,
            novel_name=args.name,
            start_chapter=args.start,
            end_chapter=args.end,
            delay=args.delay
        )


if __name__ == "__main__":
    main()
