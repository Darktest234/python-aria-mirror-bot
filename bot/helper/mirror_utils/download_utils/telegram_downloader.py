import logging
import threading
import time

from bot import (
    LOGGER,
    TELEGRAM_API,
    TELEGRAM_HASH,
    USER_SESSION_STRING,
    app,
    download_dict,
    download_dict_lock,
)
from pyrogram import Client

from ..status_utils.telegram_download_status import TelegramDownloadStatus
from .download_helper import DownloadHelper

global_lock = threading.Lock()
GLOBAL_GID = set()

logging.getLogger("pyrogram").setLevel(logging.WARNING)


class TelegramDownloadHelper(DownloadHelper):
    def __init__(self, listener):
        super().__init__()
        self.__listener = listener
        self.__resource_lock = threading.RLock()
        self.__name = ""
        self.__gid = ""
        self.__start_time = time.time()
        self.__user_bot = app
        self.__is_cancelled = False

    @property
    def gid(self):
        with self.__resource_lock:
            return self.__gid

    @property
    def download_speed(self):
        with self.__resource_lock:
            return self.downloaded_bytes / (time.time() - self.__start_time)

    def __onDownloadStart(self, name, size, file_id):
        with download_dict_lock:
            download_dict[self.__listener.uid] = TelegramDownloadStatus(
                self, self.__listener
            )
        with global_lock:
            GLOBAL_GID.add(file_id)
        with self.__resource_lock:
            self.name = name
            self.size = size
            self.__gid = file_id
        self.__listener.onDownloadStarted()

    def __onDownloadProgress(self, current, total):
        if self.__is_cancelled:
            self.__onDownloadError("Cancelled by user!")
            self.__bot.stop_transmission()
            return
        with self.__resource_lock:
            self.downloaded_bytes = current
            try:
                self.progress = current / self.size * 100
            except ZeroDivisionError:
                self.progress = 0

    def __onDownloadError(self, error):
        with global_lock:
            try:
                GLOBAL_GID.remove(self.gid)
            except KeyError:
                pass
        self.__listener.onDownloadError(error)

    def __onDownloadComplete(self):
        with global_lock:
            GLOBAL_GID.remove(self.gid)
        self.__listener.onDownloadComplete()

    def __download(self, message, path):
        download = self.__bot.download_media(
            message, progress=self.__onDownloadProgress, file_name=path
        )
        if download is not None:
            self.__onDownloadComplete()
        else:
            if not self.__is_cancelled:
                self.__onDownloadError("Internal error occurred")

    def add_download(self, message, path, filename):
        _message = self.__bot.get_messages(message.chat.id, message.message_id)
        media = None
        media_array = [_message.document, _message.video, _message.audio]
        for i in media_array:
            if i is not None:
                media = i
                break
        if media is not None:
            with global_lock:
                # For avoiding locking the thread lock for long time unnecessarily
                download = media.file_id not in GLOBAL_GID
            if filename == "":
                name = media.file_name
            else:
                name = filename
                path = path + name
            if download:
                self.__onDownloadStart(name, media.file_size, media.file_id)
                LOGGER.info(f"Downloading telegram file with id: {media.file_id}")
                threading.Thread(target=self.__download, args=(_message, path)).start()
            else:
                self.__onDownloadError("File already being downloaded!")
        else:
            self.__onDownloadError("No document in the replied message")

    def cancel_download(self):
        LOGGER.info(f"Cancelling download on user request: {self.gid}")
        self.__is_cancelled = True
