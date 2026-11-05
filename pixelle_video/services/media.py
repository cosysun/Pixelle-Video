"""Media generation using direct provider APIs only."""
from pixelle_video.config import config_manager
from .api_media import APIProviderMediaService


class MediaService(APIProviderMediaService):
    async def __call__(self, prompt, workflow=None, **params):
        workflow = workflow or config_manager.config.media.image.default_workflow
        if not workflow or not workflow.startswith('api/'):
            raise ValueError('请选择已配置的直连 API 画面模型')
        return await super().__call__(prompt=prompt, workflow=workflow, **params)
