import asyncio
from typing import Dict, Any
import aiohttp

from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools
from astrbot.api import logger
from astrbot.api.message_components import File
from astrbot.api.all import command

from .utils.database import initialize_database
from .utils.subscription import SubscriptionService
from .utils.pixiv_utils import init_pixiv_utils
from .utils.help import init_help_manager, get_help_message
from .utils.llm_tool import create_pixiv_llm_tools

from .utils.config import PixivConfig, PixivConfigManager

from .core.client import PixivClientWrapper
from .handlers.illust import IllustHandler
from .handlers.user import UserHandler
from .handlers.novel import NovelHandler
from .handlers.subscribe import SubscribeHandler
from .handlers.random_illust import RandomIllustHandler


class PixivSearchPlugin(Star):
    """
    AstrBot 插件，用于通过 Pixiv API 搜索插画。
    配置通过 AstrBot WebUI 进行管理。
    用法:
        /pixiv <标签1>,<标签2>,...  搜索 Pixiv 插画
        /pixiv help                 查看帮助信息
    可在配置中设置认证信息、返回数量和 R18 过滤模式。
    """

    def __init__(self, context: Context, config: Dict[str, Any]):
        """初始化 Pixiv 插件"""
        super().__init__(context)
        self.config = config

        # 1.初始化配置管理器

        self.pixiv_config = PixivConfig(self.config)
        self.config_manager = PixivConfigManager(self.pixiv_config)

        # 2. 初始化核心客户端 (Facade 持有核心组件)
        self.client_wrapper = PixivClientWrapper(self.pixiv_config)
        self.client = self.client_wrapper.client_api

        # 3. 初始化各个子系统 (Handlers)，把工具给它们
        self.illust_handler = IllustHandler(self.client_wrapper, self.pixiv_config)
        self.user_handler = UserHandler(self.client_wrapper, self.pixiv_config)
        self.novel_handler = NovelHandler(self.client_wrapper, self.pixiv_config)
        self.subscribe_handler = SubscribeHandler(
            self.client_wrapper, self.pixiv_config
        )
        self.random_illust_handler = RandomIllustHandler(
            self.client_wrapper, self.pixiv_config, context
        )

        self._refresh_task: asyncio.Task = None
        self._http_session = None
        self.sub_service = None
        self.random_search_service = None

        # 使用 StarTools 获取标准数据目录
        data_dir = StarTools.get_data_dir("pixiv_search")
        self.temp_dir = data_dir / "temp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # 初始化 PixivUtils 模块
        init_pixiv_utils(self.client, self.pixiv_config, self.temp_dir)

        # 初始化帮助消息管理器
        init_help_manager(data_dir)

        # 初始化数据库
        initialize_database()

        # 记录初始化信息
        logger.info(f"Pixiv 插件配置加载：{self.pixiv_config.get_config_info()}")

        # 启动后台刷新任务
        asyncio.create_task(self.client_wrapper.start_refresh_task())

        # 启动订阅服务
        if self.pixiv_config.subscription_enabled:
            self.sub_service = SubscriptionService(
                self.client_wrapper, self.pixiv_config, context
            )
            self.sub_service.start()
        else:
            logger.info("Pixiv 插件：订阅功能已禁用。")

        # 启动随机搜索服务
        self.random_search_service = self.random_illust_handler.random_search_service
        self.random_search_service.start()

        # 初始化LLM工具
        logger.info(
            f"Pixiv 插件：准备初始化LLM工具，client: {'已设置' if self.client else '未设置'}"
        )
        self.llm_tools = create_pixiv_llm_tools(self.client, self.pixiv_config)
        logger.info("Pixiv 插件：LLM工具已初始化。")

        # 注册LLM工具到AstrBot
        try:
            self.context.add_llm_tools(*self.llm_tools)
            logger.info(
                f"Pixiv 插件：已注册 {len(self.llm_tools)} 个LLM工具到AstrBot系统。"
            )
        except Exception as e:
            logger.error(f"Pixiv 插件：注册LLM工具失败 - {e}")

    @staticmethod
    def info() -> Dict[str, Any]:
        """返回插件元数据"""
        return {
            "name": "pixiv_search",
            "author": "vmoranv",
            "description": "Pixiv 图片搜索",
            "version": "1.4.3",
            "homepage": "https://github.com/vmoranv-reborn/astrbot_plugin_pixiv_search",
        }

    # --------插画类
    
    @command("pixiv")
    async def pixiv_search_illust(self, event: AstrMessageEvent, tags: str = ""):
        """处理 /pixiv 命令，默认为标签搜索功能"""
        async for result in self.illust_handler.pixiv_search_illust(event, tags):
            yield result

    @command("pixiv_illust_new")
    async def pixiv_illust_new(
        self,
        event: AstrMessageEvent,
        content_type: str = "illust",
        max_illust_id: str = "",
    ):
        """获取大家的新插画作品"""
        async for result in self.illust_handler.pixiv_illust_new(
            event, content_type, max_illust_id
        ):
            yield result

    @command("pixiv_recommended")
    async def pixiv_recommended(self, event: AstrMessageEvent, args: str = ""):
        """获取 Pixiv 推荐作品"""
        async for result in self.illust_handler.pixiv_recommended(event, args):
            yield result

    @command("pixiv_and")
    async def pixiv_and(self, event: AstrMessageEvent, tags: str = ""):
        """处理 /pixiv_and 命令，进行 AND 逻辑深度搜索"""
        async for result in self.illust_handler.pixiv_and(event, tags):
            yield result

    @command("pixiv_specific")
    async def pixiv_specific(self, event: AstrMessageEvent, illust_id: str = ""):
        """根据作品 ID 获取特定作品详情"""
        async for result in self.illust_handler.pixiv_specific(event, illust_id):
            yield result

    @command("pixiv_ranking")
    async def pixiv_ranking(self, event: AstrMessageEvent, args: str = ""):
        """获取 Pixiv 排行榜作品"""
        async for result in self.illust_handler.pixiv_ranking(event, args):
            yield result

    @command("pixiv_related")
    async def pixiv_related(self, event: AstrMessageEvent, illust_id: str = ""):
        """获取与指定作品相关的其他作品"""
        async for result in self.illust_handler.pixiv_related(event, illust_id):
            yield result

    @command("pixiv_deepsearch")
    async def pixiv_deepsearch(self, event: AstrMessageEvent, tags: str):
        """
        深度搜索 Pixiv 插画，通过翻页获取多页结果
        用法: /pixiv_deepsearch <标签1>,<标签2>,...
        注意: 翻页深度由配置中的 deep_search_depth 参数控制
        """
        async for result in self.illust_handler.pixiv_deepsearch(event, tags):
            yield result

    @command("pixiv_illust_comments")
    async def pixiv_illust_comments(
        self, event: AstrMessageEvent, illust_id: str = "", offset: str = ""
    ):
        """获取指定作品的评论"""
        async for result in self.illust_handler.pixiv_illust_comments(
            event, illust_id, offset
        ):
            yield result

    @command("pixiv_showcase_article")
    async def pixiv_showcase_article(
        self, event: AstrMessageEvent, showcase_id: str = ""
    ):
        """获取特辑详情"""
        async for result in self.illust_handler.pixiv_showcase_article(
            event, showcase_id
        ):
            yield result

    # ----用户类

    @command("pixiv_user_search")
    async def pixiv_user_search(self, event: AstrMessageEvent, username: str = ""):
        """搜索 Pixiv 用户"""
        async for result in self.user_handler.pixiv_user_search(event, username):
            yield result

    @command("pixiv_user_detail")
    async def pixiv_user_detail(self, event: AstrMessageEvent, user_id: str = ""):
        """获取 Pixiv 用户详情"""
        async for result in self.user_handler.pixiv_user_detail(event, user_id):
            yield result

    @command("pixiv_user_illusts")
    async def pixiv_user_illusts(self, event: AstrMessageEvent, user_id: str = ""):
        """获取指定用户的作品"""
        async for result in self.user_handler.pixiv_user_illusts(event, user_id):
            yield result

    # --------小说类

    @command("pixiv_novel")
    async def pixiv_novel(self, event: AstrMessageEvent, tags: str = ""):
        """处理 /pixiv_novel 命令，搜索 Pixiv 小说"""
        async for result in self.novel_handler.pixiv_novel(event, tags):
            yield result

    @command("pixiv_novel_recommended")
    async def pixiv_novel_recommended(self, event: AstrMessageEvent):
        """获取 Pixiv 推荐小说"""
        async for result in self.novel_handler.pixiv_novel_recommended(event):
            yield result

    @command("pixiv_novel_new")
    async def pixiv_novel_new(self, event: AstrMessageEvent, max_novel_id: str = ""):
        """获取大家的新小说"""
        async for result in self.novel_handler.pixiv_novel_new(event, max_novel_id):
            yield result

    @command("pixiv_novel_series")
    async def pixiv_novel_series(self, event: AstrMessageEvent, series_id: str = ""):
        """获取小说系列详情"""
        async for result in self.novel_handler.pixiv_novel_series(event, series_id):
            yield result

    @command("pixiv_novel_comments")
    async def pixiv_novel_comments(
        self, event: AstrMessageEvent, novel_id: str = "", offset: str = ""
    ):
        """获取指定小说的评论"""
        async for result in self.novel_handler.pixiv_novel_comments(
            event, novel_id, offset
        ):
            yield result

    @command("pixiv_novel_download")
    async def pixiv_novel_download(self, event: AstrMessageEvent, novel_id: str = ""):
        """根据ID下载Pixiv小说为pdf文件"""
        async for result in self.novel_handler.pixiv_novel_download(event, novel_id):
            yield result

    # ----订阅类

    @command("pixiv_subscribe_add")
    async def pixiv_subscribe_add(self, event: AstrMessageEvent, artist_id: str = ""):
        """订阅画师"""
        async for result in self.subscribe_handler.pixiv_subscribe_add(
            event, artist_id
        ):
            yield result

    @command("pixiv_subscribe_remove")
    async def pixiv_subscribe_remove(
        self, event: AstrMessageEvent, artist_id: str = ""
    ):
        """取消订阅画师"""
        async for result in self.subscribe_handler.pixiv_subscribe_remove(
            event, artist_id
        ):
            yield result

    @command("pixiv_subscribe_list")
    async def pixiv_subscribe_list(self, event: AstrMessageEvent, args: str = ""):
        """查看当前订阅列表"""
        async for result in self.subscribe_handler.pixiv_subscribe_list(event, args):
            yield result

    @command("pixiv_help")
    async def pixiv_help(self, event: AstrMessageEvent, args: str = ""):
        """生成并返回帮助信息"""

        help_text = get_help_message("pixiv_help", "帮助消息加载失败，请检查配置文件。")
        yield event.plain_result(help_text)

    # ----随机搜索类

    @command("pixiv_random_add")
    async def pixiv_random_add(self, event: AstrMessageEvent, tags: str = ""):
        """添加随机搜索标签"""
        async for result in self.random_illust_handler.pixiv_random_add(event, tags):
            yield result

    @command("pixiv_random_del")
    async def pixiv_random_del(self, event: AstrMessageEvent, index: str = ""):
        """删除随机搜索标签"""
        async for result in self.random_illust_handler.pixiv_random_del(event, index):
            yield result

    @command("pixiv_random_list")
    async def pixiv_random_list(self, event: AstrMessageEvent, args: str = ""):
        """列出当前群聊/用户的随机搜索标签"""
        async for result in self.random_illust_handler.pixiv_random_list(event, args):
            yield result

    @command("pixiv_random_suspend")
    async def pixiv_random_suspend(self, event: AstrMessageEvent):
        """暂停当前群聊的随机搜索功能"""
        async for result in self.random_illust_handler.pixiv_random_suspend(event):
            yield result

    @command("pixiv_random_resume")
    async def pixiv_random_resume(self, event: AstrMessageEvent):
        """恢复当前群聊的随机搜索功能"""
        async for result in self.random_illust_handler.pixiv_random_resume(event):
            yield result

    @command("pixiv_random_status")
    async def pixiv_random_status(self, event: AstrMessageEvent):
        """查看随机搜索队列状态"""
        async for result in self.random_illust_handler.pixiv_random_status(event):
            yield result

    @command("pixiv_random_force")
    async def pixiv_random_force(self, event: AstrMessageEvent):
        """强制执行当前群聊的随机搜索（调试用）"""
        async for result in self.random_illust_handler.pixiv_random_force(event):
            yield result

    @command("pixiv_trending_tags")
    async def pixiv_trending_tags(self, event: AstrMessageEvent):
        """获取 Pixiv 插画趋势标签"""
        logger.info("Pixiv 插件：正在获取插画趋势标签...")

        # 验证是否已认证
        if not await self._authenticate():
            yield event.plain_result(self.pixiv_config.get_auth_error_message())
            return

        try:
            # 调用 API 获取趋势标签
            result = await self._call_pixiv_api(
                self.client.trending_tags_illust, filter="for_ios"
            )  # 默认使用 for_ios, 也可以尝试 for_android

            if not result or not result.trend_tags:
                yield event.plain_result("未能获取到趋势标签，可能是 API 暂无数据。")
                return

            # 格式化标签信息
            tags_list = []
            for tag_info in result.trend_tags:
                tag_name = tag_info.get("tag", "未知标签")
                translated_name = tag_info.get("translated_name")
                if translated_name and translated_name != tag_name:
                    tags_list.append(f"- {tag_name} ({translated_name})")
                else:
                    tags_list.append(f"- {tag_name}")

            if not tags_list:
                yield event.plain_result("未能解析任何趋势标签。")
                return

            # 构建最终消息
            message = "# Pixiv 插画趋势标签\n\n"
            message += "\n".join(tags_list)

            yield event.plain_result(message)

        except Exception as e:
            logger.error(f"Pixiv 插件：获取趋势标签时发生错误 - {e}")
            yield event.plain_result(f"获取趋势标签时发生错误: {str(e)}")

    @command("pixiv_ai_show_settings")
    async def pixiv_ai_show_settings(self, event: AstrMessageEvent, setting: str = ""):
        """设置是否展示AI生成作品"""
        # 检查是否为帮助请求
        if not setting.strip() or setting.strip().lower() == "help":
            help_text = get_help_message(
                "pixiv_ai_show_settings", "AI作品设置帮助消息加载失败，请检查配置文件。"
            )
            yield event.plain_result(help_text)
            return

        # 验证设置参数
        valid_settings = ["true", "false", "1", "0", "yes", "no", "on", "off"]
        if setting.lower() not in valid_settings:
            yield event.plain_result(
                f"无效的设置值: {setting}\n可用值: {', '.join(valid_settings)}"
            )
            return

        # 转换为字符串 "true" 或 "false" (API要求)
        setting_str = (
            "true" if setting.lower() in ["true", "1", "yes", "on"] else "false"
        )

        # 验证是否已认证
        if not await self._authenticate():
            yield event.plain_result(self.pixiv_config.get_auth_error_message())
            return

        logger.info(f"Pixiv 插件：正在设置AI作品显示 - 设置: {setting_str}")

        try:
            # 使用 asyncio.to_thread 包装同步 API 调用
            result = await self._call_pixiv_api(
                self.client.user_edit_ai_show_settings, setting=setting_str
            )

            if result and hasattr(result, "error") and result.error:
                yield event.plain_result(
                    f"设置AI作品显示失败: {result.error.get('message', '未知错误')}"
                )
                return

            # 同时更新本地配置
            if setting_str == "true":
                self.pixiv_config.ai_filter_mode = "显示 AI 作品"
                mode_desc = "显示AI作品"
            else:
                self.pixiv_config.ai_filter_mode = "过滤 AI 作品"
                mode_desc = "过滤AI作品"

            # 保存配置
            self.pixiv_config.save_config()

            yield event.plain_result(
                f"AI作品设置已更新为: {mode_desc}\n本地配置已同步更新。"
            )

        except Exception as e:
            logger.error(f"Pixiv 插件：设置AI作品显示时发生错误 - {e}")
            import traceback

            logger.error(traceback.format_exc())
            yield event.plain_result(f"设置AI作品显示时发生错误: {str(e)}")

    @command("pixiv_config")
    async def pixiv_config(
        self, event: AstrMessageEvent, arg1: str = "", arg2: str = ""
    ):
        """查看或动态设置 Pixiv 插件参数（除 refresh_token）。"""
        # 使用配置管理器处理命令
        result = await self.config_manager.handle_config_command(event, arg1, arg2)
        if result:
            yield event.plain_result(result)

    async def terminate(self):
        """插件终止时调用的清理函数"""
        logger.info("Pixiv 搜索插件正在停用...")
        # 停止订阅服务
        if self.sub_service:
            self.sub_service.stop()
        # 取消后台刷新任务
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                # 等待任务实际取消
                await self._refresh_task
            except asyncio.CancelledError:
                logger.info("Pixiv Token 刷新任务已成功取消。")
            except Exception as e:
                logger.error(f"等待 Pixiv Token 刷新任务取消时发生错误: {e}")

        logger.info("Pixiv 搜索插件已停用。")
        # 关闭HTTP会话
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()

    async def _get_http_session(self):
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    async def pixiv_llm_search(self, query: str, search_type: str = "illust") -> str:
        """
        使用LLM工具进行智能搜索

        Args:
            query: 搜索查询，可以是自然语言描述
            search_type: 搜索类型，如 'illust', 'novel', 'user' 等

        Returns:
            str: 搜索结果
        """
        try:
            # 验证是否已认证
            if not await self._authenticate():
                return self.pixiv_config.get_auth_error_message()

            logger.info(
                f"Pixiv 插件：使用LLM工具搜索 - 查询: {query}, 类型: {search_type}"
            )

            # 使用PixivSearchTool进行搜索
            search_tool = None
            for tool in self.llm_tools:
                if hasattr(tool, "name") and tool.name == "pixiv_search":
                    search_tool = tool
                    break

            if not search_tool:
                return "LLM搜索工具未初始化"

            # 创建模拟的上下文
            from astrbot.core.agent.run_context import ContextWrapper
            from astrbot.core.astr_agent_context import AstrAgentContext

            mock_context = ContextWrapper(AstrAgentContext())

            # 调用搜索工具
            result = await search_tool.call(
                mock_context, query=query, search_type=search_type
            )

            logger.info("Pixiv 插件：LLM搜索完成")
            return result

        except Exception as e:
            error_msg = f"LLM搜索时发生错误: {str(e)}"
            logger.error(error_msg)
            return error_msg
