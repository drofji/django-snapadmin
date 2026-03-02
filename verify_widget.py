import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(viewport={'width': 1280, 'height': 1000})
        page = await context.new_page()

        # Login
        await page.goto('http://localhost:8000/admin/login/')
        await page.fill('input[name="username"]', 'admin')
        await page.fill('input[name="password"]', 'admin')
        await page.click('button[type="submit"]')

        # Go to API Token add page
        await page.goto('http://localhost:8000/admin/snapadmin/apitoken/add/')
        await page.wait_for_timeout(2000)
        await page.screenshot(path='/home/jules/verification/apitoken_add_fixed.png', full_page=True)

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
