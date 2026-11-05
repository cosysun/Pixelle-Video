"""Video module; shared platform services are injected by the application."""

from pixelle_video.config import config_manager
from pixelle_video.pipelines.standard import StandardPipeline
from pixelle_video.services.frame_processor import FrameProcessor
from pixelle_video.services.history_manager import HistoryManager
from pixelle_video.services.llm_service import LLMService
from pixelle_video.services.media import MediaService
from pixelle_video.services.persistence import PersistenceService
from pixelle_video.services.task_editor import TaskEditService
from pixelle_video.services.tts_service import TTSService
from pixelle_video.services.video import VideoService


class PixelleVideoCore:
    def __init__(self, config_path="config.yaml", persistence=None):
        self.config = config_manager.config.to_dict()
        self.persistence = persistence
        self._initialized = False

    async def initialize(self):
        if self._initialized:
            return
        self.llm = LLMService(self.config)
        self.tts = TTSService(self.config, core=self)
        self.media = MediaService(self.config, core=self)
        self.api_media = self.media
        self.image = self.media
        self.video = VideoService()
        self.frame_processor = FrameProcessor(self)
        self.persistence = self.persistence or PersistenceService()
        self.task_editor = TaskEditService(self)
        self.history = HistoryManager(self.persistence, task_editor=self.task_editor)
        self.pipelines = {"standard": StandardPipeline(self)}
        self._initialized = True

    async def generate_video(self, text, pipeline="standard", **params):
        if pipeline not in {"standard", "quick_create"}:
            raise ValueError("Unsupported video pipeline")
        return await self.pipelines["standard"](text=text, **params)

    async def cleanup(self):
        from pixelle_video.services.frame_html import HTMLFrameGenerator

        await HTMLFrameGenerator.close_browser()

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, *args):
        await self.cleanup()


pixelle_video = PixelleVideoCore()
