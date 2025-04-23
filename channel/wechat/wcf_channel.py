# encoding:utf-8

"""
wechat channel
"""

import io
import json
import os
import threading
import time
from queue import Empty
from typing import Any

from bridge.context import *
from bridge.reply import *
from channel.chat_channel import ChatChannel
from channel.wechat.wcf_message import WechatfMessage
from common.log import logger
from common.singleton import singleton
from common.utils import *
from config import conf, get_appdata_dir
from wcferry import Wcf, WxMsg


@singleton
class WechatfChannel(ChatChannel):
    NOT_SUPPORT_REPLYTYPE = []

    def __init__(self):
        super().__init__()
        self.NOT_SUPPORT_REPLYTYPE = []
        # 使用字典存储最近消息，用于去重
        self.received_msgs = {}
        # 初始化wcferry客户端
        self.wcf = Wcf()
        self.wxid = None  # 登录后会被设置为当前登录用户的wxid

    def startup(self):
        """
        启动通道
        """
        try:
            # wcferry会自动唤起微信并登录
            self.wxid = self.wcf.get_self_wxid()
            self.name = self.wcf.get_user_info().get("name")
            logger.info(f"微信登录成功，当前用户ID: {self.wxid}, 用户名：{self.name}")
            self.contact_cache = ContactCache(self.wcf)
            self.contact_cache.update()
            # 启动消息接收
            self.wcf.enable_receiving_msg()
            # 创建消息处理线程
            t = threading.Thread(target=self._process_messages, name="WeChatThread", daemon=True)
            t.start()


        except Exception as e:
            logger.error(f"微信通道启动失败: {e}")
            raise e

    def _process_messages(self):
        """
        处理消息队列
        """
        while True:
            try:
                msg = self.wcf.get_msg()
                if msg:
                    # 添加详细日志，记录接收到的消息
                    # 打印WxMsg对象的所有属性名称，帮助调试
                    logger.info(f"接收到消息对象属性: {dir(msg)}")
                    
                    # 安全地访问属性，避免属性不存在的错误
                    msg_info = {
                        "id": getattr(msg, 'id', 'Unknown'),
                        "type": getattr(msg, 'type', 'Unknown'),
                        "sender": getattr(msg, 'sender', 'Unknown'),
                        "content_length": len(getattr(msg, 'content', '')) if hasattr(msg, 'content') else 0
                    }
                    
                    logger.info(f"接收到消息: {msg_info}")
                    
                    if hasattr(msg, 'content') and msg.content and len(msg.content) < 500:
                        logger.info(f"消息内容: {msg.content}")
                    self._handle_message(msg)
            except Empty:
                continue
            except Exception as e:
                logger.error(f"处理消息失败: {e}")
                continue

    def _handle_message(self, msg: WxMsg):
        """
        处理单条消息
        """
        try:
            # 构造消息对象
            cmsg = WechatfMessage(self, msg)
            # 消息去重
            if cmsg.msg_id in self.received_msgs:
                return
            self.received_msgs[cmsg.msg_id] = time.time()
            # 清理过期消息ID
            self._clean_expired_msgs()

            logger.debug(f"收到消息: {msg}")
            context = self._compose_context(cmsg.ctype, cmsg.content,
                                            isgroup=cmsg.is_group,
                                            msg=cmsg)
            if context:
                self.produce(context)
        except NotImplementedError as e:
            # 添加更详细的日志信息，但保持原始逻辑
            logger.error(f"处理消息失败: 不支持的消息类型 {msg.type}, 消息ID: {msg.id}, 发送者: {msg.sender}, 内容长度: {len(msg.content) if hasattr(msg, 'content') else 'N/A'}")
        except Exception as e:
            logger.error(f"处理消息失败: {e}, 消息类型: {msg.type if hasattr(msg, 'type') else 'Unknown'}")

    def _clean_expired_msgs(self, expire_time: float = 60):
        """
        清理过期的消息ID
        """
        now = time.time()
        for msg_id in list(self.received_msgs.keys()):
            if now - self.received_msgs[msg_id] > expire_time:
                del self.received_msgs[msg_id]

    def send(self, reply: Reply, context: Context):
        """
        发送消息
        """
        receiver = context["receiver"]
        if not receiver:
            logger.error("receiver is empty")
            return

        # 添加详细日志，记录准备发送的消息
        logger.info(f"准备发送消息到微信: 接收者={receiver}, 消息类型={reply.type}, 内容长度={len(reply.content) if reply.content else 0}")
        if reply.content and len(reply.content) < 500:
            logger.info(f"发送内容: {reply.content}")
        logger.info(f"上下文信息: 会话标识={context.get('session_id', 'None')}, 是否群聊={context.get('isgroup', False)}")

        try:
            if reply.type == ReplyType.TEXT:
                # 处理@信息
                at_list = []
                if context.get("isgroup"):
                    if context["msg"].actual_user_id:
                        at_list = [context["msg"].actual_user_id]
                at_str = ",".join(at_list) if at_list else ""
                logger.info(f"调用微信发送接口: send_text, 接收者={receiver}, @列表={at_str}")
                self.wcf.send_text(reply.content, receiver, at_str)
                logger.info(f"消息发送成功: 接收者={receiver}")

            elif reply.type == ReplyType.ERROR or reply.type == ReplyType.INFO:
                logger.info(f"调用微信发送接口: send_text(错误/信息类型), 接收者={receiver}")
                self.wcf.send_text(reply.content, receiver)
                logger.info(f"消息发送成功: 接收者={receiver}")
            else:
                logger.error(f"暂不支持的消息类型: {reply.type}")

        except Exception as e:
            logger.error(f"发送消息失败: {e}")

    def close(self):
        """
        关闭通道
        """
        try:
            self.wcf.cleanup()
        except Exception as e:
            logger.error(f"关闭通道失败: {e}")


class ContactCache:
    def __init__(self, wcf):
        """
        wcf: 一个 wcfferry.client.Wcf 实例
        """
        self.wcf = wcf
        self._contact_map = {}  # 形如 {wxid: {完整联系人信息}}

    def update(self):
        """
        更新缓存：调用 get_contacts()，
        再把 wcf.contacts 构建成 {wxid: {完整信息}} 的字典
        """
        self.wcf.get_contacts()
        self._contact_map.clear()
        for item in self.wcf.contacts:
            wxid = item.get('wxid')
            if wxid:  # 确保有 wxid 字段
                self._contact_map[wxid] = item

    def get_contact(self, wxid: str) -> dict:
        """
        返回该 wxid 对应的完整联系人 dict，
        如果没找到就返回 None
        """
        return self._contact_map.get(wxid)

    def get_name_by_wxid(self, wxid: str) -> str:
        """
        通过wxid，获取成员/群名称
        """
        contact = self.get_contact(wxid)
        if contact:
            return contact.get('name', '')
        return ''