from asyncio import Task, sleep, Event, create_task, gather, TimeoutError as AsyncTimeoutError
from random import choice
from os import environ
from os.path import join
from typing import Optional
from json import loads, JSONDecodeError
from http.client import RemoteDisconnected
from urllib3.exceptions import ProtocolError, ReadTimeoutError
from aiofiles import open as aio_open
from logging import getLogger, Logger
from contextlib import suppress


from playwright.async_api import (
    Error as PlaywrightError, 
    TimeoutError as PlaywrightTimeoutError,
)
from playwright._impl._errors import TargetClosedError

from ..utils.exceptions import BrowserClosedError, ElementNotFound
from .SeparateInstance import SeparateInstance
from .Profiles import Profiles
from ..utils.clear_ram import clear_ram

logger: Logger = getLogger(f"streamstorm.{__name__}")

class StreamStorm(Profiles): # removed Selenium inheritance coz its doing nothing
    __slots__: tuple[str, ...] = (
        'url', 'chat_url', 'messages', 'subscribe', 'subscribe_and_wait_time', 
        'slow_mode', 'channels', 'background', 'ready_event', 'pause_event',
        'total_instances', 'ready_to_storm_instances', 'total_channels', 
        'all_channels', 'assigned_profiles', 'run_stopper_event'
    )
    
    each_channel_instances: list[SeparateInstance] = []
    ss_instance: Optional["StreamStorm"] = None

    def __init__(
        self,
        url: str,
        chat_url: str,
        messages: list[str],
        channels: list[int],
        subscribe: tuple[bool, bool] = (False, False),
        subscribe_and_wait_time: int = 70,
        slow_mode: int = 0,
        background: bool = True
    ) -> None:
        
        super().__init__()

        self.url: str = f"{url}&hl=en-US&persist_hl=1"
        self.chat_url: str = f"{chat_url}&hl=en-US&persist_hl=1"
        self.messages: list[str] = messages
        self.subscribe: tuple[bool, bool] = subscribe
        self.subscribe_and_wait_time: int = subscribe_and_wait_time
        self.slow_mode: int = slow_mode
        self.channels: list[int] = sorted(channels)
        self.background: bool = background

        self.ready_event: Event = Event()
        self.pause_event: Event = Event()
        self.run_stopper_event: Event = Event()

        self.total_instances: int = len(channels)
        self.ready_to_storm_instances: int = 0
        self.total_channels: int = 0
        self.all_channels: dict[str, dict[str, str]] = {}

        self.assigned_profiles: dict[str, int] = {}

        StreamStorm.ss_instance = self

        logger.debug(f"StreamStorm initialized with url: {self.url}, channels: {self.channels}, "
                    f"messages count: {len(self.messages)}, slow_mode: {self.slow_mode}s, "
                    f"background: {self.background}")        
        
        
    async def set_slow_mode(self, slow_mode: int) -> None:
        self.slow_mode = slow_mode
        logger.info(f"Slow mode set to {self.slow_mode} seconds")

    async def set_messages(self, messages: list[str]) -> None:
        self.messages = messages
        logger.info(f"Messages set to: {self.messages}")


    async def check_channels_available(self) -> None:
        logger.debug(f"Checking channel availability for profiles in: {self.profiles_dir}")
        
        try:
            async with aio_open(join(self.profiles_dir, "data.json"), "r", encoding="utf-8") as file:
                data: dict = loads(await file.read()) # We are using loads instead of load to avoid blocking the event loop
        except (FileNotFoundError, PermissionError, UnicodeDecodeError, JSONDecodeError) as e:
            logger.debug("Failed to read data.json - profiles not created yet")
            raise SystemError("Create profiles first.") from e
            
        no_of_channels: int = data.get("no_of_channels", 0)

        self.total_channels = no_of_channels
        self.all_channels = data.get("channels", {})
        
        logger.debug(f"Found {no_of_channels} channels in config, required: {len(self.channels)}")

        if no_of_channels < len(self.channels):
            logger.debug(f"Insufficient channels: available={no_of_channels}, required={len(self.channels)}")
            raise SystemError("Not enough channels available in your YouTube Account. Create enough channels first. Then create Profiles again in the app.")
    
    async def get_active_channels(self) -> list[int]:
        active_channels: list[int] = []

        active_channels.extend(
            channel_index
            for channel_index in self.assigned_profiles.values()
            if channel_index is not None
        )
        return active_channels
        
    async def EachChannel(self, index: int, profile_dir: str, wait_time: float = 0) -> None:
        
        channel_name: str = self.all_channels[str(index)]['name']

        logger.info(f"[{index}] [{channel_name}] Using profile: {profile_dir}, Wait time: {wait_time}s")
        profile_dir_name: str=  profile_dir.split("\\")[-1]
        
        try:

            SI = SeparateInstance(
                index,
                profile_dir,
                self.background,
            )

            SI.channel_name = channel_name

            self.assigned_profiles[profile_dir_name] = index

            logger.info(f"[{index}] [{channel_name}] Assigned profile {profile_dir_name}")

            StreamStorm.each_channel_instances.append(SI)
            
            logger.debug(f"[{index}] [{channel_name}] Attempting login...")
            logged_in: bool = await SI.login()
            
            self.run_stopper_event.set()  # Set the event to signal that stopper can check for instance errors
            logger.debug(f"[{index}] [{channel_name}] Run stopper event set")
            
            if not logged_in:
                logger.debug(f"[{index}] [{channel_name}] Login failed - removing from instances")
                
                self.total_instances -= 1
                self.assigned_profiles[profile_dir_name] = None
                
                StreamStorm.each_channel_instances.remove(SI)
                
                logger.error(f"[{index}] [{channel_name}] : Login failed")
                
                return

            logger.debug(f"[{index}] [{channel_name}] Login successful")

            if self.subscribe[0]:
                logger.debug(f"[{index}] [{channel_name}] Navigating to subscribe URL: {self.url}")
                
                await SI.go_to_page(self.url)
                await SI.subscribe_to_channel()
                
                logger.debug(f"[{index}] [{channel_name}] Subscription attempt completed")

            await SI.page.set_viewport_size({"width": 500, "height": 900})
            logger.debug(f"[{index}] [{channel_name}] Navigating to chat URL: {self.chat_url}")
            await SI.go_to_page(self.chat_url)
            
            self.ready_to_storm_instances += 1
            logger.info(f"[{index}] [{channel_name}] : Ready To Storm")

            if self.subscribe[1]:
                logger.debug(f"[{index}] [{channel_name}] Waiting {self.subscribe_and_wait_time}s after subscription")
                await sleep(self.subscribe_and_wait_time)
                 
                
            logger.debug(f"[{index}] [{channel_name}] Waiting for ready event...")
            await self.ready_event.wait() # Wait for the ready event to be set before starting the storming

            logger.debug(f"[{index}] [{channel_name}] Starting storm loop with {wait_time}s initial delay")
            await sleep(wait_time)  # Wait for the initial delay before starting to storm

            while True:
                await self.pause_event.wait()
        
                # input()
                selected_message = choice(self.messages)
                logger.debug(f"[{index}] [{channel_name}] Sending message: '{selected_message}'")
                
                try:
                    await SI.send_message(selected_message)
                    logger.debug(f"[{index}] [{channel_name}] Message sent successfully")
                    
                except (BrowserClosedError, ElementNotFound, TargetClosedError):
                    logger.debug(f"[{index}] [{channel_name}] : ##### Browser/element error - cleaning up instance")
                    logger.error(f"[{index}] [{channel_name}] : Error in finding chat field")

                    self.assigned_profiles[profile_dir_name] = None
                    
                    try:
                        await SI.page.close()
                    except PlaywrightError as e:
                        logger.error(f"[{index}] [{channel_name}] : Error closing page: {e}")
                    
                    with suppress(ValueError):
                        StreamStorm.each_channel_instances.remove(SI)
                        logger.debug(f"[{index}] [{channel_name}] : Removed from instances")                        
                    
                    break
                    
                except Exception as e:
                    logger.error(f"[{index}] [{channel_name}] : New Error ({type(e).__name__}): {e}")
                    break
                
                logger.debug(f"[{index}] [{channel_name}] Sleeping for {self.slow_mode}s before next message")
                await sleep(self.slow_mode)

        except (
            RemoteDisconnected,
            ProtocolError,
            ReadTimeoutError,
            ConnectionResetError,
            TimeoutError,
            AsyncTimeoutError,
            PlaywrightError,
            PlaywrightTimeoutError,
            BrowserClosedError
        ) as e:
            logger.error(f"[{index}] [{channel_name}] : Error: {e}")
        
    def get_start_storm_wait_time(self, index, no_of_profiles, slow_mode) -> float:
        return index * (slow_mode / no_of_profiles)

    async def start(self) -> None:  
        
        clear_ram()       

        self.ready_event.clear()  # Wait for the ready event to be set before starting the storming
        self.ready_to_storm_instances = 0
        
        await self.check_channels_available()
        
        if self.channels[-1] > self.total_channels:
            raise SystemError("You have selected more channels than available channels in your YouTube channel. Create enough channels first.")
        
        
        temp_profiles: list[str] = self.get_available_temp_profiles()
        no_of_temp_profiles: int = len(temp_profiles)
        
        self.assigned_profiles = {profile: None for profile in temp_profiles}
        
        
        
        if no_of_temp_profiles < len(self.channels):
            raise SystemError("Not enough temp profiles available. Create Enough profiles first.")       
        

        async def start_each_worker() -> None:

            async def wait_for_all_worker_to_be_ready() -> None:
                while self.ready_to_storm_instances < self.total_instances:
                    await sleep(1)
                logger.debug(f"All {self.total_instances} instances ready - starting storm")
                self.ready_event.set()  # Set the event to signal that all instances are ready

            create_task(wait_for_all_worker_to_be_ready())
            
            tasks: list[Task] = []
            for index in range(len(self.channels)):
                profile_dir: str = join(self.profiles_dir, temp_profiles[index])
                wait_time: float = self.get_start_storm_wait_time(index, no_of_temp_profiles, self.slow_mode)

                # executor.submit(self.EachChannel, self.channels[index], profile_dir, wait_time)
                task: Task = create_task(self.EachChannel(self.channels[index], profile_dir, wait_time))
                tasks.append(task)
                await sleep(0.2)  # Small delay to avoid instant spike of the cpu load

            await gather(*tasks)  # Wait for all tasks to complete
            environ.update({"BUSY": "0"})

            self.run_stopper_event.clear()
            StreamStorm.ss_instance = None
            StreamStorm.each_channel_instances.clear()
            logger.info("All Coroutines completed")

        create_task(start_each_worker())
    
    async def start_more_channels(self, channels: list[int]) -> None:
        
        async def get_profiles() -> tuple[bool, list[str]]:
            count: int = 0
            available_profiles: list[str] = []
            for key, value in self.assigned_profiles.items():
                if value is None:
                    count += 1
                    available_profiles.append(key)

            return count >= len(channels), available_profiles[:len(channels)]

        enough_profiles, available_profiles = await get_profiles()

        already_running_channels: list[int] = self.assigned_profiles.values()

        for channel in channels:
            if channel in already_running_channels:
                raise SystemError(f"Channel {channel} : {self.all_channels[str(channel)]['name']} is already running.")
        if not enough_profiles:
            raise SystemError("Not enough available profiles to start more channels.")
        
        async def start_each_worker() -> None:  
            
            tasks: list[Task] = []   
            for index in range(len(channels)):
                profile_dir: str = join(self.profiles_dir, available_profiles[index])
                wait_time: float = self.get_start_storm_wait_time(index, len(available_profiles), self.slow_mode)

                task: Task = create_task(self.EachChannel(channels[index], profile_dir, wait_time))
                tasks.append(task)
                await sleep(0.2)

            await gather(*tasks)
            logger.info("All Coroutines completed")

        create_task(start_each_worker())       


__all__: list[str] = ["StreamStorm"]