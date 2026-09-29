import asyncio

from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp("http://127.0.0.1:9222")
        pages = [page for context in browser.contexts for page in context.pages]
        page = next(page for page in pages if "meet.google.com" in page.url)
        print((await page.locator("body").inner_text())[-8000:])


asyncio.run(main())
