"""
models/message.py —— 消息的 pydantic 数据模型

Message 是 MessageBus（消息总线）中传递的基本单元。
每个 Message 被序列化为一行 JSON（JSONL 格式），
追加到接收方的收件箱文件中。

设计要点：
- from 是 Python 关键字，使用 alias 重命名为 from_
- 所有时间戳使用 float（time.time()），简单够用
- type 字段有白名单，由 VALID_MSG_TYPES 校验
"""

import time

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    """
    消息模型 —— agent 间通信的基本单元。

    消息类型说明：
    - message             —— 普通点对点消息
    - broadcast           —— 向所有队友广播
    - shutdown_request    —— 请求关机
    - shutdown_response   —— 关机响应
    - plan_approval_response —— 计划审批结果
    """
    model_config = ConfigDict(populate_by_name=True)

    type: str = Field(
        default="message",
        description="消息类型",
    )
    from_: str = Field(
        ...,
        alias="from",   # "from" 是 Python 保留字，模型内用 from_，序列化时自动转回 "from"
        description="发送者名称",
    )
    content: str = Field(
        ...,
        min_length=1,
        description="消息正文",
    )
    timestamp: float = Field(
        default_factory=time.time,
        description="发送时间戳（Unix epoch）",
    )
