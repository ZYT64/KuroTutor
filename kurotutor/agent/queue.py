"""优先级消息队列 + 语境感知打断（产品规格书 4.5）。

优先级：P0 学生新消息（最高）→ P1 后续 → P2 推送（不抢对话）→ P3 后台。

无感体验设计：
- P0 到达时，任何待发送的推送（P2）向后延迟，避免打断学生。
- 同优先级内先进先出；跨优先级高者先出。
- 支持把某个任务标记为「被打断」并移出队列，缓存可恢复（由调度层读取缓存重放）。
"""

from __future__ import annotations

import heapq
import itertools
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class Priority(IntEnum):
    """消息优先级。数值越小越先处理。"""

    P0 = 0  # 学生新消息
    P1 = 1  # 学生后续（如追问）
    P2 = 2  # 推送（复习提醒/开课提醒等，不抢对话）
    P3 = 3  # 后台


@dataclass(order=True)
class _Node:
    priority: int
    seq: int  # 入队顺序，同优先级下先进先出
    item: Any = field(compare=False)
    meta: dict = field(default_factory=dict, compare=False)


class PriorityQueue:
    """线程安全的最小堆优先队列，基于 ``Condition`` 实现阻塞等待与唤醒。"""

    def __init__(self) -> None:
        self._heap: list[_Node] = []
        self._counter = itertools.count()
        self._interrupted: dict[str, dict] = {}
        self._cond = threading.Condition()

    def put(self, item: Any, priority: Priority, *, key: str | None = None, meta: dict | None = None) -> None:
        """入队。``key`` 用于去重/幂等；``meta`` 附加信息。"""
        with self._cond:
            node = _Node(priority=int(priority), seq=next(self._counter), item=item, meta=meta or {})
            if key:
                node.meta["key"] = key
            heapq.heappush(self._heap, node)
            self._cond.notify()

    def get(self, timeout: float | None = None) -> Any | None:
        """出队最高优先级；队列空且超过 ``timeout`` 后返回 None。"""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while not self._heap:
                if deadline is None:
                    self._cond.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(timeout=remaining)
            return heapq.heappop(self._heap).item

    def peek(self) -> tuple[Any, Priority] | None:
        """查看最高优先级任务而不出队。"""
        with self._cond:
            if not self._heap:
                return None
            node = self._heap[0]
            return node.item, Priority(node.priority)

    def interrupt(self, key: str, payload: dict) -> None:
        """把某任务标记为打断，暂存状态以便恢复，并从队列移除对应待发任务。"""
        with self._cond:
            self._interrupted[key] = payload
            self._heap = [n for n in self._heap if n.meta.get("key") != key]
            heapq.heapify(self._heap)
            self._cond.notify()

    def restore(self, key: str) -> dict | None:
        """取出并清空某个被打断任务的缓存。"""
        with self._cond:
            return self._interrupted.pop(key, None)

    def pending_interruptions(self) -> list[str]:
        with self._cond:
            return list(self._interrupted.keys())

    def __len__(self) -> int:
        with self._cond:
            return len(self._heap)
