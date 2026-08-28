"""优先级队列与打断测试。"""

from __future__ import annotations

import time

from kurotutor.agent.queue import Priority, PriorityQueue


def test_priority_order_fifo_within_level():
    q = PriorityQueue()
    q.put("normal", Priority.P0)
    q.put("push1", Priority.P2)
    q.put("push2", Priority.P3)
    q.put("echo", Priority.P1)
    assert q.get() == "normal"
    assert q.get() == "echo"
    assert q.get() == "push1"
    assert q.get() == "push2"


def test_fifo_within_same_priority():
    q = PriorityQueue()
    q.put("a", Priority.P0)
    q.put("b", Priority.P0)
    q.put("c", Priority.P0)
    assert [q.get(), q.get(), q.get()] == ["a", "b", "c"]


def test_get_returns_none_on_timeout():
    q = PriorityQueue()
    start = time.monotonic()
    result = q.get(timeout=0.05)
    assert result is None
    assert time.monotonic() - start < 1.0


def test_interrupt_removes_queued_and_restores():
    q = PriorityQueue()
    q.put("push", Priority.P2, key="review-1", meta={"task": "review"})
    q.put("student", Priority.P0)
    q.interrupt("review-1", payload={"resume": True})
    # 被打断的推送被移除，学生消息不受影响
    assert q.get() == "student"
    assert q.get(timeout=0.05) is None
    assert q.pending_interruptions() == ["review-1"]
    restored = q.restore("review-1")
    assert restored == {"resume": True}
    assert q.pending_interruptions() == []


def test_len():
    q = PriorityQueue()
    q.put("a", Priority.P0)
    q.put("b", Priority.P2)
    assert len(q) == 2
    q.get()
    assert len(q) == 1
