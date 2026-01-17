#!/usr/bin/env python3
"""
Test script - scrape just first 3 chapters from each site
"""

from scrape_novels import EmpireNovelScraper, NovelBinScraper


def test_empire_novel():
    """Test empirenovel.com with 3 chapters"""
    print("\n" + "="*60)
    print("TEST: The Beginning After The End (empirenovel.com)")
    print("Testing chapters 1-3")
    print("="*60)

    scraper = EmpireNovelScraper(output_dir="novels")
    successful, failed = scraper.scrape_range(
        novel_slug="the-beginning-after-the-end",
        novel_name="The Beginning After The End",
        start_chapter=1,
        end_chapter=3,
        delay=2
    )
    return successful, failed


def test_novelbin():
    """Test novelbin.com with 3 chapters"""
    print("\n" + "="*60)
    print("TEST: Dragon's Egg (novelbin.com)")
    print("Testing chapters 1-3")
    print("="*60)

    scraper = NovelBinScraper(output_dir="novels")
    successful, failed = scraper.scrape_range(
        novel_slug="reincarnated-as-a-dragons-egg-lets-aim-to-be-the-strongest",
        novel_name="Reincarnated as a Dragons Egg",
        start_chapter=1,
        end_chapter=3,
        delay=2
    )
    return successful, failed


if __name__ == "__main__":
    print("="*60)
    print("Novel Scraper Test Run")
    print("="*60)

    # Test empirenovel.com
    empire_success, empire_fail = test_empire_novel()

    # Test novelbin.com
    novelbin_success, novelbin_fail = test_novelbin()

    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    print(f"EmpireNovel: {empire_success}/3 successful, {empire_fail} failed")
    print(f"NovelBin:    {novelbin_success}/3 successful, {novelbin_fail} failed")
    print("="*60)

    if empire_success > 0 and novelbin_success > 0:
        print("\nBoth sites working! Ready for full scrape.")
        print("Run: uv run scrape_novels.py")
    elif empire_success > 0:
        print("\nEmpireNovel working, NovelBin has issues.")
    elif novelbin_success > 0:
        print("\nNovelBin working, EmpireNovel has issues.")
    else:
        print("\nBoth sites have issues. Check output above.")
