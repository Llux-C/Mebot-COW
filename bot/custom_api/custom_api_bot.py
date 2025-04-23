# encoding:utf-8

import json
import time
import requests

from bot.bot import Bot
from bot.session_manager import SessionManager
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from config import conf

class CustomAPISession:
    def __init__(self, session_id, system_prompt=None):
        self.session_id = session_id
        self.messages = []
        # 不使用system prompt

    # 添加用户消息
    def add_query(self, query):
        self.messages.append({"role": "user", "content": query})
        return self

    # 添加AI回复消息
    def add_reply(self, reply):
        self.messages.append({"role": "assistant", "content": reply})
        return self

    # 获取所有消息
    def get_messages(self):
        return self.messages

    # 清空消息历史
    def clear(self):
        self.messages = []
        return self

    # 转换为字符串
    def __str__(self):
        return json.dumps(self.messages, ensure_ascii=False)


class CustomAPIBot(Bot):
    def __init__(self):
        super().__init__()
        # 从配置文件中读取API地址和其他参数
        self.api_url = conf().get("custom_api_url", "")
        self.api_key = conf().get("custom_api_key", "")
        self.timeout = conf().get("custom_api_timeout", 30)
        
        # 初始化会话管理器
        self.sessions = SessionManager(CustomAPISession)

    def reply(self, query, context=None):
        """
        处理用户查询并返回回复
        :param query: 用户输入的查询文本
        :param context: 上下文信息
        :return: 回复对象
        """
        if context and context.type:
            if context.type == ContextType.TEXT:
                logger.info("[CUSTOM_API] query={}".format(query))
                session_id = context["session_id"]
                reply = None
                
                # 处理特殊命令
                if query == "#清除记忆":
                    self.sessions.clear_session(session_id)
                    reply = Reply(ReplyType.INFO, "记忆已清除")
                elif query == "#清除所有":
                    self.sessions.clear_all_session()
                    reply = Reply(ReplyType.INFO, "所有人记忆已清除")
                else:
                    # 获取会话并添加用户查询
                    session = self.sessions.session_query(query, session_id)
                    
                    # 调用自定义API获取回复
                    result = self.call_custom_api(session)
                    
                    if result["success"]:
                        reply_content = result["content"]
                        # 将AI回复添加到会话中
                        self.sessions.session_reply(reply_content, session_id)
                        reply = Reply(ReplyType.TEXT, reply_content)
                    else:
                        # 处理API调用失败的情况
                        reply = Reply(ReplyType.ERROR, result["content"])
                
                return reply
            
            elif context.type == ContextType.IMAGE_CREATE:
                # 图像创建功能暂不支持
                return Reply(ReplyType.ERROR, "自定义API暂不支持图像创建")
        
        return Reply(ReplyType.ERROR, "未知的查询类型")

    def call_custom_api(self, session):
        """
        调用自定义API获取回复
        :param session: 会话对象
        :return: 包含成功状态和内容的字典
        """
        try:
            # 检查API URL是否配置
            if not self.api_url:
                return {"success": False, "content": "未配置自定义API URL，请在config.json中设置custom_api_url"}
            
            # 准备请求数据
            headers = {
                "Content-Type": "application/json"
            }
            
            # 如果有API Key，添加到请求头
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            
            # 获取用户ID
            userId = conf().get("custom_api_userId", "")
            
            # 准备请求体
            data = {
                "messages": session.get_messages(),
                "userId": userId
            }
            
            # 发送请求
            logger.debug(f"[CUSTOM_API] Sending request to {self.api_url}")
            response = requests.post(
                self.api_url,
                headers=headers,
                json=data,
                timeout=self.timeout
            )
            
            # 检查响应状态
            if response.status_code == 200:
                try:
                    result = response.json()
                    # 假设 API返回格式为 {"response": "回复内容"}
                    # 根据你的实际API调整这里的解析逻辑
                    if "response" in result:
                        return {"success": True, "content": result["response"]}
                    else:
                        logger.error(f"[CUSTOM_API] Unexpected response format: {result}")
                        return {"success": False, "content": "API返回格式不正确"}
                except json.JSONDecodeError:
                    logger.error(f"[CUSTOM_API] Failed to parse response as JSON: {response.text}")
                    return {"success": False, "content": "API返回的不是有效的JSON格式"}
            else:
                logger.error(f"[CUSTOM_API] API request failed with status {response.status_code}: {response.text}")
                return {"success": False, "content": f"API请求失败，状态码: {response.status_code}"}
                
        except requests.RequestException as e:
            logger.error(f"[CUSTOM_API] Request exception: {e}")
            return {"success": False, "content": f"API请求异常: {str(e)}"}
        except Exception as e:
            logger.error(f"[CUSTOM_API] Unexpected error: {e}")
            return {"success": False, "content": f"发生未知错误: {str(e)}"}
