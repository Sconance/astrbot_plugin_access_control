from sys import maxsize
import re

import astrbot.core.message.components as Comp
from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register
from astrbot.core import AstrBotConfig
from astrbot.core.platform import AstrMessageEvent
from astrbot.core.star.filter.event_message_type import EventMessageType


@register(
    "astrbot_plugin_access_control",
    "Sconance",
    "限制机器人响应范围，并允许管理员通过群命令维护用户权限",
    "1.1.0",
)
class AccessControlPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    @filter.event_message_type(EventMessageType.ALL, priority=maxsize - 5)
    async def permission_commands(self, event: AstrMessageEvent):
        """Bot管理员通过@或QQ号禁用、启用用户的机器人触发权限。"""
        plain_text = " ".join(
            segment.text.strip()
            for segment in event.get_messages()
            if isinstance(segment, Comp.Plain) and segment.text.strip()
        ).strip().removeprefix("/").strip()
        if plain_text in {"访问控制帮助", "查看禁用名单"}:
            event.stop_event()
            if not event.is_admin():
                yield event.plain_result("只有Bot管理员可以查看访问控制帮助和禁用名单")
                return
            if plain_text == "访问控制帮助":
                yield event.plain_result(
                    "访问控制帮助\n"
                    "访问控制帮助：查看功能及管理命令。\n"
                    "查看禁用名单：查看禁用用户及相关开关状态。\n"
                    "权限禁用 @成员 / 禁用 QQ号：加入禁用名单并开启用户黑名单。\n"
                    "权限启用 @成员 / 启用 QQ号：移出禁用名单。\n"
                    "命令支持 / 前缀。\n\n"
                    "访问控制开启后，按用户黑名单、私聊开关、群白名单和群聊 @ 对象过滤设置处理消息。\n"
                    "启用用户后，仍需符合其他访问规则；关闭总开关时，禁用名单暂不生效。\n"
                    "以上命令仅限 AstrBot 管理员使用。"
                )
            else:
                blocked = self._normalize_ids(self.config.get("blocked_sender_qqs", []))
                enabled = self.config.get("enabled", True)
                sender_enabled = self.config.get("blocked_sender_enabled", False)
                status = (
                    f"访问控制：{'开启' if enabled else '关闭'}\n"
                    f"用户黑名单：{'开启' if sender_enabled else '关闭'}"
                )
                listing = (
                    "禁用用户（共 " + str(len(blocked)) + " 人）：\n"
                    + "\n".join(blocked)
                    if blocked else "当前没有禁用用户"
                )
                note = (
                    "\n当前禁用名单暂不生效：访问控制或用户黑名单未开启。"
                    if blocked and not (enabled and sender_enabled) else ""
                )
                yield event.plain_result(status + "\n\n" + listing + note)
            return

        at_targets = [
            str(segment.qq)
            for segment in event.get_messages()
            if isinstance(segment, Comp.At)
            and str(segment.qq) not in {"all", str(event.get_self_id())}
        ]

        action = None
        target_qq = at_targets[0] if at_targets else None
        if plain_text in {"权限禁用", "禁用权限", "禁用"} and target_qq:
            action = "disable"
        elif plain_text in {"权限启用", "启用权限", "启用"} and target_qq:
            action = "enable"
        else:
            match = re.fullmatch(
                r"(?:权限)?(禁用|启用)\s*([1-9][0-9]{4,11})",
                plain_text,
            ) or re.fullmatch(
                r"([1-9][0-9]{4,11})\s*(禁用)",
                plain_text,
            )
            if match:
                first, second = match.groups()
                if first in {"禁用", "启用"}:
                    action = "disable" if first == "禁用" else "enable"
                    target_qq = second
                else:
                    target_qq = first
                    action = "disable"

        if not action or not target_qq:
            return

        event.stop_event()
        if not event.is_admin():
            yield event.plain_result("只有Bot管理员可以修改用户权限")
            return

        if target_qq == str(event.get_self_id()):
            yield event.plain_result("不能修改机器人自身的触发权限")
            return
        if action == "disable" and target_qq == str(event.get_sender_id()):
            yield event.plain_result("不能禁用当前操作者；可以使用权限启用恢复自己的权限")
            return

        blocked = self._normalize_ids(self.config.get("blocked_sender_qqs", []))
        was_blocked = target_qq in blocked
        changed = False
        if action == "disable":
            if not was_blocked:
                blocked.append(target_qq)
            if not self.config.get("blocked_sender_enabled", False):
                self.config["blocked_sender_enabled"] = True
                changed = True
        else:
            blocked = [qq for qq in blocked if qq != target_qq]
        if self.config.get("blocked_sender_qqs", []) != blocked:
            self.config["blocked_sender_qqs"] = blocked
            changed = True
        if changed:
            self.config.save_config()

        if action == "disable":
            if not self.config.get("enabled", True):
                response = " 已加入禁用名单；启用访问控制后生效"
            else:
                response = " 已被禁用" if not was_blocked else " 已在禁用名单中"
        else:
            response = " 已从禁用名单移除" if was_blocked else " 未被禁用"
        yield event.chain_result([
            Comp.At(qq=target_qq, name=target_qq),
            Comp.Plain(text=response),
        ])

    @staticmethod
    def _normalize_ids(values):
        """统一编号格式，去掉空值和重复项，并保留原有顺序。"""
        return list(dict.fromkeys(
            str(value).strip() for value in values
            if value is not None and str(value).strip()
        ))

    @filter.event_message_type(EventMessageType.ALL, priority=maxsize - 10)
    async def guard_messages(self, event: AstrMessageEvent):
        """检查发送者忽略名单、允许的群聊、私聊开关及屏蔽的 @ 对象；不符合规则时停止后续消息处理。"""
        if not self.config.get("enabled", True):
            return

        group_id = str(event.get_group_id() or "")

        if self.config.get("blocked_sender_enabled", False):
            blocked_senders = set(self._normalize_ids(
                self.config.get("blocked_sender_qqs", [])
            ))
            sender_id = str(event.get_sender_id() or "").strip()
            if sender_id and sender_id in blocked_senders:
                logger.info("发送者 QQ %s 位于忽略名单，停止处理", sender_id)
                event.stop_event()
                return

        # 私聊没有群号；此开关默认允许私聊继续交给其他功能插件处理。
        if not group_id:
            if not self.config.get("allow_private", True):
                event.stop_event()
            return

        allowed_groups = set(self._normalize_ids(
            self.config.get("allowed_groups", [])
        ))
        if group_id not in allowed_groups:
            event.stop_event()
            return

        if not self.config.get("blocked_at_enabled", True):
            return

        blocked_qq = str(self.config.get("blocked_at_qq", "")).strip()
        if blocked_qq and any(
            isinstance(segment, Comp.At) and str(segment.qq) == blocked_qq
            for segment in event.get_messages()
        ):
            logger.info("消息实际 @ 到已屏蔽 QQ %s，停止处理", blocked_qq)
            event.stop_event()
