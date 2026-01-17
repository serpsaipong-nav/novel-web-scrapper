import requests
from bs4 import BeautifulSoup
import os
import time
import re
import json

class OffsetAwareScraper:
    def __init__(self, novel_name, base_output_dir="novels"):
        self.novel_name = novel_name
        self.clean_novel_name = self.sanitize_folder_name(novel_name)
        self.output_dir = os.path.join(base_output_dir, self.clean_novel_name)
        os.makedirs(self.output_dir, exist_ok=True)
        
        # Create reports directory
        self.reports_dir = os.path.join(self.output_dir, "reports")
        os.makedirs(self.reports_dir, exist_ok=True)
        
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        # Novel URL mapping
        self.novel_urls = {
            'my dragon system': 'https://freewebnovel.com/novel/my-dragon-system/chapter-{chapter}',
            'my vampire system': 'https://freewebnovel.com/novel/my-vampire-system/chapter-{chapter}',
            'my werewolf system': 'https://freewebnovel.com/novel/my-werewolf-system/chapter-{chapter}',
        }
        
        self.url_pattern = self.get_novel_url(novel_name)
        
        # Offset tracking - maps chapter number to URL offset
        self.offset_map = {}
        self.detected_offsets = []
        
    def sanitize_folder_name(self, name):
        """Convert novel name to valid folder name"""
        clean_name = re.sub(r'[<>:"/\\|?*]', '', name)
        clean_name = re.sub(r'\s+', '_', clean_name)
        return clean_name.lower()
    
    def get_novel_url(self, novel_name):
        """Get URL pattern for the novel"""
        novel_key = novel_name.lower().strip()
        
        if novel_key in self.novel_urls:
            return self.novel_urls[novel_key]
        else:
            novel_slug = novel_key.replace(' ', '-')
            return f'https://freewebnovel.com/novel/{novel_slug}/chapter-{{chapter}}'
    
    def extract_chapter_number_from_content(self, soup):
        """Extract the actual chapter number from the content"""
        # Try to find chapter number in title or content
        title = soup.title.get_text() if soup.title else ""
        
        # Look for "Chapter X" patterns
        patterns = [
            r'Chapter\s+(\d+)',
            r'Ch\.?\s*(\d+)',
            r'chapter-(\d+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, title, re.IGNORECASE)
            if match:
                return int(match.group(1))
        
        # Try to find in first paragraph
        paragraphs = soup.find_all('p')
        if paragraphs:
            first_p = paragraphs[0].get_text()
            for pattern in patterns:
                match = re.search(pattern, first_p, re.IGNORECASE)
                if match:
                    return int(match.group(1))
        
        return None
    
    def detect_offset_for_chapter(self, target_chapter, max_offset_search=10):
        """Detect the URL offset needed to find the target chapter"""
        print(f"🔍 Detecting offset for Chapter {target_chapter}...")
        
        # Try different offsets: 0, +1, +2, +3, -1, -2, -3
        offsets_to_try = [0] + list(range(1, max_offset_search + 1)) + list(range(-1, -max_offset_search - 1, -1))
        
        for offset in offsets_to_try:
            url_chapter = target_chapter + offset
            if url_chapter <= 0:
                continue
                
            url = self.url_pattern.format(chapter=url_chapter)
            
            try:
                response = requests.get(url, headers=self.headers, timeout=8)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'html.parser')
                    actual_chapter = self.extract_chapter_number_from_content(soup)
                    
                    if actual_chapter == target_chapter:
                        print(f"   ✅ Found Chapter {target_chapter} at URL chapter-{url_chapter} (offset: {offset:+d})")
                        return offset
                    else:
                        print(f"   ❌ URL chapter-{url_chapter} contains Chapter {actual_chapter} (offset: {offset:+d})")
                else:
                    print(f"   ❌ URL chapter-{url_chapter}: HTTP {response.status_code} (offset: {offset:+d})")
            except Exception as e:
                print(f"   ❌ URL chapter-{url_chapter}: Error {str(e)[:20]}... (offset: {offset:+d})")
            
            time.sleep(0.5)  # Be respectful
        
        print(f"   💔 Could not find Chapter {target_chapter} within offset range ±{max_offset_search}")
        return None
    
    def get_url_for_chapter(self, chapter_num):
        """Get the correct URL for a chapter, accounting for offsets"""
        # Check if we have a known offset for this chapter range
        best_offset = 0
        
        for offset_chapter in sorted(self.offset_map.keys(), reverse=True):
            if chapter_num >= offset_chapter:
                best_offset = self.offset_map[offset_chapter]
                break
        
        url_chapter = chapter_num + best_offset
        return self.url_pattern.format(chapter=url_chapter), url_chapter, best_offset
    
    def extract_chapter_content(self, paragraphs, verbose=True):
        """Extract clean chapter content from paragraphs"""
        content_parts = []
        
        for i, p in enumerate(paragraphs):
            text = p.get_text(strip=True)
            
            if len(text) > 15:
                skip_phrases = [
                    'prev chapter', 'next chapter', 'use arrow keys',
                    'report chapter', 'freewebnovel.com', 'contact',
                    'sitemap', 'privacy policy', 'log in', 'create account',
                    'tap the screen', 'add to library', 'submit', 'comments',
                    'welcome to freewebnovel', 'don\'t have an account'
                ]
                
                should_skip = any(skip in text.lower() for skip in skip_phrases)
                
                if not should_skip:
                    content_parts.append(text)
        
        content = '\n\n'.join(content_parts)
        
        if verbose:
            print(f"   📊 Extracted {len(content_parts)} paragraphs, {len(content)} characters")
        
        return content
    
    def scrape_chapter(self, chapter_num):
        """Scrape a single chapter using offset-aware logic"""
        # First, try using known offsets
        url, url_chapter, offset = self.get_url_for_chapter(chapter_num)
        
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                actual_chapter = self.extract_chapter_number_from_content(soup)
                
                # Check if we got the right chapter
                if actual_chapter == chapter_num:
                    # Perfect match!
                    content = self.extract_chapter_content(soup.find_all('p'))
                    
                    if content and len(content) > 100:
                        # Extract title
                        page_title = soup.title.get_text() if soup.title else f"Chapter {chapter_num}"
                        
                        if " - Chapter " in page_title:
                            chapter_title = page_title.split(" - Chapter ")[1].split(" | ")[0]
                            chapter_title = f"Chapter {chapter_title}"
                        elif "Chapter" in page_title:
                            chapter_title = page_title.split(" | ")[0]
                            for novel_name_variant in [self.novel_name, self.novel_name.title()]:
                                if novel_name_variant in chapter_title:
                                    chapter_title = chapter_title.replace(f"{novel_name_variant} - ", "")
                                    break
                        else:
                            chapter_title = f"Chapter {chapter_num}"
                        
                        offset_info = f" (offset: {offset:+d})" if offset != 0 else ""
                        print(f"✅ Chapter {chapter_num}: {chapter_title} ({len(content)} chars){offset_info}")
                        
                        return chapter_title, content
                    else:
                        print(f"❌ Chapter {chapter_num}: No content at URL chapter-{url_chapter}")
                else:
                    # Wrong chapter - need to detect new offset
                    print(f"⚠️ Chapter {chapter_num}: Expected {chapter_num}, got {actual_chapter} at URL chapter-{url_chapter}")
                    
                    # Detect correct offset for this chapter
                    correct_offset = self.detect_offset_for_chapter(chapter_num)
                    
                    if correct_offset is not None:
                        # Update offset map
                        self.offset_map[chapter_num] = correct_offset
                        self.detected_offsets.append({
                            'chapter': chapter_num,
                            'offset': correct_offset,
                            'reason': f'Expected {chapter_num}, found {actual_chapter}'
                        })
                        
                        # Try again with correct offset
                        url, url_chapter, offset = self.get_url_for_chapter(chapter_num)
                        return self.scrape_chapter(chapter_num)
                    else:
                        print(f"💔 Chapter {chapter_num}: Could not find correct URL")
                        return None, None
            else:
                print(f"❌ Chapter {chapter_num}: HTTP {response.status_code} at URL chapter-{url_chapter}")
                return None, None
                
        except Exception as e:
            print(f"❌ Chapter {chapter_num}: {e}")
            return None, None
    
    def save_chapter(self, title, content, chapter_num):
        """Save chapter as markdown file"""
        clean_title = re.sub(r'[<>:"/\\|?*]', '', title)
        clean_title = re.sub(r'\s+', ' ', clean_title).strip()
        
        filename = f"Chapter {chapter_num:03d} - {clean_title}.md"
        filepath = os.path.join(self.output_dir, filename)
        
        markdown_content = f"# {title}\n\n{content}\n"
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(markdown_content)
            print(f"💾 Saved: {filename}")
            return True
        except Exception as e:
            print(f"❌ Error saving: {e}")
            return False
    
    def scrape_range(self, start, end, delay=2):
        """Scrape a range of chapters with automatic offset detection"""
        print(f"🚀 Scraping '{self.novel_name}' chapters {start} to {end}")
        print(f"📁 Saving to: {self.output_dir}")
        print(f"🔍 Auto-detecting URL offsets...")
        print()
        
        successful = 0
        failed = 0
        
        for chapter_num in range(start, end + 1):
            title, content = self.scrape_chapter(chapter_num)
            
            if content:
                if self.save_chapter(title, content, chapter_num):
                    successful += 1
                else:
                    failed += 1
            else:
                failed += 1
            
            # Add delay between chapters
            if chapter_num < end:
                time.sleep(delay)
        
        # Generate offset report
        self.generate_offset_report(start, end, successful, failed)
        
        return successful, failed
    
    def generate_offset_report(self, start, end, successful, failed):
        """Generate a report showing detected offsets"""
        print(f"\n📊 SCRAPING REPORT")
        print(f"{'='*50}")
        print(f"📖 Novel: {self.novel_name}")
        print(f"🎯 Range: Chapters {start} to {end}")
        print(f"✅ Successful: {successful}")
        print(f"❌ Failed: {failed}")
        
        if self.detected_offsets:
            print(f"\n🔧 DETECTED URL OFFSETS:")
            for offset_info in self.detected_offsets:
                print(f"   Chapter {offset_info['chapter']}: offset {offset_info['offset']:+d} ({offset_info['reason']})")
            
            print(f"\n💡 OFFSET PATTERN:")
            for chapter, offset in sorted(self.offset_map.items()):
                url_chapter = chapter + offset
                print(f"   Chapter {chapter} → URL chapter-{url_chapter} (offset: {offset:+d})")
        
        # Save offset map for future use
        offset_file = os.path.join(self.reports_dir, f"offset_map_{start}-{end}.json")
        with open(offset_file, 'w') as f:
            json.dump({
                'novel': self.novel_name,
                'offset_map': self.offset_map,
                'detected_offsets': self.detected_offsets
            }, f, indent=2)
        
        print(f"\n📁 Offset report saved: {offset_file}")

def main():
    print("🤖 Offset-Aware Novel Scraper")
    print("Automatically detects and handles URL offsets")
    print("=" * 50)
    
    # Get novel name
    novel_name = input("Enter novel name: ").strip()
    
    if not novel_name:
        print("❌ Novel name cannot be empty!")
        return
    
    scraper = OffsetAwareScraper(novel_name)
    
    print(f"\n📖 Novel: {novel_name}")
    print(f"📁 Output folder: {scraper.output_dir}")
    
    # Get range to scrape
    print(f"\n📖 RANGE SELECTION")
    print("The scraper will auto-detect URL offsets as it goes")
    
    try:
        start = int(input("Start chapter (e.g., 360): "))
        end = int(input("End chapter (e.g., 365): "))
        delay = float(input("Delay in seconds (default 2): ") or "2")
        
        if start > end:
            print("❌ Start must be less than end")
            return
        
        # Run scraping with offset detection
        successful, failed = scraper.scrape_range(start, end, delay)
        
        if successful > 0:
            print(f"\n🎉 Successfully scraped {successful} chapters!")
            print(f"📁 Files saved in: {scraper.output_dir}")
        
        if scraper.offset_map:
            print(f"\n💡 TIP: The scraper learned the offset pattern.")
            print(f"You can now scrape larger ranges efficiently!")
        
    except ValueError:
        print("❌ Invalid input")
    except KeyboardInterrupt:
        print("\n⏸️ Scraping interrupted by user")

if __name__ == "__main__":
    main()
