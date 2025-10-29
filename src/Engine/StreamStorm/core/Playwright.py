from asyncio import Lock, create_task
from logging import getLogger, Logger
from playwright.async_api import (
    async_playwright,
    Playwright as AsyncPlaywright,
    TimeoutError as PlaywrightTimeoutError,
)
from playwright.async_api._generated import Browser, BrowserContext, Locator, Page

from .BrowserAutomator import BrowserAutomator
from ..utils.exceptions import BrowserClosedError, ElementNotFound

logger: Logger = getLogger(f"streamstorm.{__name__}")

class Playwright(BrowserAutomator):
    __slots__: tuple[str, ...] = (
        'user_data_dir', 'background', '_Playwright__instance_alive', 
        'playwright', 'browser', 'page', 'index', 'channel_name'
    )
    
    _chrome_version: str = None
    _version_lock: Lock = Lock()

    def __init__(self, user_data_dir: str, background: bool) -> None:
        self.user_data_dir: str = user_data_dir
        self.background: bool = background

    async def __get_chromium_options(self) -> dict[str, str | bool | list[str]]:
        options: dict[str, str | bool | list[str]] = {
            "user_data_dir": self.user_data_dir,
            "headless": self.background,
            "args": [
                "--autoplay-policy=user-gesture-required",
                "--blink-settings=imagesEnabled=false",
                "--disable-animations",
                "--disable-default-apps",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-logging",
                "--disable-notifications",
                "--enable-low-end-device-mode",
                "--hide-scrollbars",
                "--metrics-recording-only",
                "--mute-audio",
                "--no-sandbox",
                "--password-store=basic",
            ],
            "viewport": {"width": 1200, "height": 800},
            "channel": "chrome"
        }
        
        if self.background:
            options["args"].extend([
                "--disable-software-rasterizer",
                "--enable-unsafe-swiftshader",
                "--use-angle=swiftshader",
                "--use-gl=angle"
            ])

        return options

    async def __close_about_blank_page(self) -> None:
        """Close the about:blank page if it exists."""
        try:
            for page in self.browser.pages:
                if page.url == "about:blank":
                    await page.close()
                    break
        except Exception as e:
            logger.error(f"[{self.index}] [{self.channel_name}] : Error closing about:blank page: {e}")

    @classmethod
    async def __get_browser_version(cls, playwright: AsyncPlaywright):
        if cls._chrome_version is None:
            async with cls._version_lock:
                if cls._chrome_version is None: # We are checking two times because multiple coroutines could be trying to access this at the same time
                    browser_obj: Browser = await playwright.chromium.launch(channel="chrome")
                    cls._chrome_version = browser_obj.version
                    await browser_obj.close()
                    
        return cls._chrome_version
    
    def _attach_error_listeners(self):
        def mark_dead(marker: str):
            self.__instance_alive = False
            logger.info(f"[{self.index}] [{self.channel_name}] : ##### StreamStorm instance marked as dead by: {marker}")

        try:
            self.page.on("close", lambda _: mark_dead("page.on_close"))
            self.page.on("crash", lambda _: mark_dead("page.on_crash"))
            self.browser.on("close", lambda _: mark_dead("browser.on_close"))
            self.browser.browser.on("disconnected", lambda _: mark_dead("browser.browser.on_disconnected"))
        except Exception as _:
            self.__instance_alive = False
            logger.info(f"[{self.index}] [{self.channel_name}] : ##### StreamStorm instance marked as dead by: Failure to attach error listeners")
            
    async def check_language_english(self) -> bool:
        language: str = await self.page.evaluate("navigator.language")        
        
        return language.startswith("en-")
        
    def change_language(self):
        raise NotImplementedError
    

    async def open_browser(self) -> None:
        self.playwright: AsyncPlaywright = await async_playwright().start()

        browser_options: dict[str, str | bool | list[str]] = await self.__get_chromium_options()
        logger.debug(f"[{self.index}] [{self.channel_name}] Browser options configured with {len(browser_options['args'])} arguments")
        logger.debug(f"[{self.index}] [{self.channel_name}] Browser arguments: {'\n'.join(browser_options['args'])}")
        
        self.browser: BrowserContext = (
            await self.playwright.chromium.launch_persistent_context(
                **browser_options
            )
        )

        self.page: Page = await self.browser.new_page()
        create_task(self.__close_about_blank_page())

        self.page.set_default_navigation_timeout(45000)
        self.page.set_default_timeout(45000)
        
        browser_version: str = await self.__get_browser_version(self.playwright)
        logger.debug(f"[{self.index}] [{self.channel_name}] Browser version: {browser_version}")
        
        await self.page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          f"(KHTML, like Gecko) Chrome/{browser_version} Safari/537.36"
        })

        self._attach_error_listeners()
        logger.debug(f"[{self.index}] [{self.channel_name}] Browser setup completed")


    async def go_to_page(self, url: str) -> None:
        
        try:
            await self.page.goto(url)
            logger.debug(f"[{self.index}] [{self.channel_name}] Successfully navigated to: {url}")
            
        except PlaywrightTimeoutError as e:
            logger.error(f"[{self.index}] [{self.channel_name}] : Timeout error occurred while navigating to {url}: {e}")
            await self.page.close(
                reason="Browser closed due to timeout error"
            )
            raise BrowserClosedError from e

    async def find_element(self, selector: str, selector_name: str) -> Locator:
        """Find an element on the page."""
        logger.debug(f"[{self.index}] [{self.channel_name}] Looking for element: {selector_name} : {selector}")

        element: Locator = self.page.locator(selector)
        try:
            await element.wait_for(state="visible")
            logger.debug(f"[{self.index}] [{self.channel_name}] Element found: {selector_name} : {selector}")
        except PlaywrightTimeoutError as e:
            logger.debug(f"[{self.index}] [{self.channel_name}] Element not found: {selector_name} : {selector}")
            raise ElementNotFound from e

        return element

    async def find_and_click_element(
        self, selector: str, selector_name: str, for_subscribe: bool = False
    ) -> None:
        """Find an element and click it."""

        try:
            element: Locator = await self.find_element(selector, selector_name)
            await element.click()
        except ElementNotFound as e:
            if not for_subscribe:
                logger.error(f"[{self.index}] [{self.channel_name}] Element not found: {selector_name} : {selector}")
                
                await self.page.close(
                    reason="Browser closed due to element not found. Element: "
                    + selector
                )
                raise BrowserClosedError from e

    async def type_and_enter(self, text_field: Locator, message: str) -> None:
        """Type a message into a text field and press enter."""
        logger.debug(f"[{self.index}] [{self.channel_name}] Typing message into field: '{message[:50]}{'...' if len(message) > 50 else ''}'")

        await text_field.fill(message)
        await text_field.press("Enter")
        
        logger.debug(f"[{self.index}] [{self.channel_name}] Message typed and Enter pressed")


__all__: list[str] = ["Playwright"]

