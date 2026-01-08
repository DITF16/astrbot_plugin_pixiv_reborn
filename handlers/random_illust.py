from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger

from ..utils.tag import validate_and_process_tags

from ..utils.random_search import RandomSearchService

from ..utils.database import (
    add_random_tag,
    remove_random_tag,
    get_random_tags,
    suspend_random_search,
    resume_random_search,
    get_random_search_status,
)


class RandomIllustHandler:

    def __init__(self, client_wrapper, pixiv_config, context):

        self.client_wrapper = client_wrapper
        self.client = client_wrapper.client_api
        self.pixiv_config = pixiv_config

        self.random_search_service = RandomSearchService(
            client_wrapper, pixiv_config, context
        )

    async def pixiv_random_add(self, event: AstrMessageEvent, tags: str = ""):
        """添加随机搜索标签"""
        cleaned_tags = tags.strip()
        if not cleaned_tags:
            yield event.plain_result("请输入要添加的随机搜索标签。")
            return

        # 验证标签格式 (借用 validate_and_process_tags 进行基本检查)
        tag_result = validate_and_process_tags(cleaned_tags)
        if not tag_result["success"]:
            yield event.plain_result(tag_result["error_message"])
            return

        chat_id = event.get_group_id() or event.get_sender_id()
        # 构造用于发送消息的 session_id
        session_id = event.unified_msg_origin

        success, message = add_random_tag(chat_id, session_id, cleaned_tags)
        yield event.plain_result(message)

    async def pixiv_random_del(self, event: AstrMessageEvent, index: str = ""):
        """删除随机搜索标签"""
        if not index.isdigit():
            yield event.plain_result(
                "请输入要删除的标签序号 (数字)。可通过 /pixiv_random_list 查看。"
            )
            return

        idx = int(index) - 1  # 转换为 0-indexed
        chat_id = event.get_group_id() or event.get_sender_id()

        success, message = remove_random_tag(chat_id, idx)
        yield event.plain_result(message)

    async def pixiv_random_list(self, event: AstrMessageEvent, args: str = ""):
        """列出当前群聊/用户的随机搜索标签"""
        chat_id = event.get_group_id() or event.get_sender_id()
        tags = get_random_tags(chat_id)

        if not tags:
            yield event.plain_result("当前没有任何随机搜索标签。")
            return

        msg = "当前随机搜索标签列表：\n"
        for i, tag_entry in enumerate(tags):
            msg += f"{i+1}. {tag_entry.tag}\n"

        yield event.plain_result(msg)

    async def pixiv_random_suspend(self, event: AstrMessageEvent):
        """暂停当前群聊的随机搜索功能"""
        chat_id = event.get_group_id() or event.get_sender_id()

        # 检查是否有配置随机搜索
        has_config, is_suspended = get_random_search_status(chat_id)
        if not has_config:
            yield event.plain_result("当前群聊没有配置随机搜索标签。")
            return

        if is_suspended:
            yield event.plain_result("当前群聊的随机搜索已经处于暂停状态。")
            return

        success, message = suspend_random_search(chat_id)
        if success:
            # 同时暂停随机搜索服务中的调度
            self.random_search_service.suspend_group_search(chat_id)
        yield event.plain_result(message)

    async def pixiv_random_resume(self, event: AstrMessageEvent):
        """恢复当前群聊的随机搜索功能"""
        chat_id = event.get_group_id() or event.get_sender_id()

        # 检查是否有配置随机搜索
        has_config, is_suspended = get_random_search_status(chat_id)
        if not has_config:
            yield event.plain_result("当前群聊没有配置随机搜索标签。")
            return

        if not is_suspended:
            yield event.plain_result("当前群聊的随机搜索已经处于运行状态。")
            return

        success, message = resume_random_search(chat_id)
        if success:
            # 同时恢复随机搜索服务中的调度
            self.random_search_service.resume_group_search(chat_id)
        yield event.plain_result(message)

    async def pixiv_random_status(self, event: AstrMessageEvent):
        """查看随机搜索队列状态"""
        status = self.random_search_service.get_queue_status()

        msg = "随机搜索队列状态：\n"
        msg += f"队列大小: {status['queue_size']}\n"
        msg += f"队列处理器运行中: {'是' if status['is_queue_processor_running'] else '否'}\n"
        msg += f"活跃群组数量: {len(status['active_groups'])}\n"

        if status["active_groups"]:
            msg += f"活跃群组: {', '.join(status['active_groups'])}\n"

        msg += f"总锁状态: {len(status['execution_locks'])} 个群组\n"

        yield event.plain_result(msg)

    async def pixiv_random_force(self, event: AstrMessageEvent):
        """强制执行当前群聊的随机搜索（调试用）"""
        chat_id = event.get_group_id() or event.get_sender_id()

        # 检查是否有配置随机搜索
        has_config, is_suspended = get_random_search_status(chat_id)
        if not has_config:
            yield event.plain_result("当前群聊没有配置随机搜索标签。")
            return

        success = await self.random_search_service.force_execute_group(chat_id)
        if success:
            yield event.plain_result("已强制将当前群聊加入执行队列，请稍等...")
        else:
            yield event.plain_result("强制执行失败，群组可能正在执行中。")
