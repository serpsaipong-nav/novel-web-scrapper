#!/usr/bin/env python3
"""
Multi-site Novel Scraper using cloudscraper
Supports: novelbin.me/novelbin.com (both simple and title-based URLs)
Outputs Obsidian-compatible markdown with tags and index
"""

import os
import re
import time
import cloudscraper
from bs4 import BeautifulSoup


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


def main():
    """CLI interface for scraping novels"""
    import argparse

    parser = argparse.ArgumentParser(description='Novel Scraper for Obsidian')
    parser.add_argument('--url', '-u', required=True, help='Novel URL (freewebnovel.com)')
    parser.add_argument('--name', '-n', required=True, help='Novel name for folder/tags')
    parser.add_argument('--start', '-s', type=int, default=1, help='Start chapter (default: 1)')
    parser.add_argument('--end', '-e', type=int, required=True, help='End chapter')
    parser.add_argument('--output', '-o', default='novels_obsidian', help='Output directory (default: novels_obsidian)')
    parser.add_argument('--delay', '-d', type=float, default=1.5, help='Delay between requests (default: 1.5)')

    args = parser.parse_args()

    # Extract slug from URL
    # Supports: https://freewebnovel.com/novel-name.html or https://freewebnovel.com/novel/novel-name/...
    slug = args.url.rstrip('/').split('/')[-1].replace('.html', '')
    if slug.startswith('chapter-'):
        slug = args.url.rstrip('/').split('/')[-2]

    print("="*60)
    print("Novel Scraper for Obsidian")
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
