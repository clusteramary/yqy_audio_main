#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import threading
import time
from typing import Optional

import config
from audio_constants import ACTION_INDEX_BY_KEYWORD

try:
    import rospy
    from std_msgs.msg import Int32

    _HAS_ROS1 = True
except Exception:
    rospy = None
    Int32 = None
    _HAS_ROS1 = False


INDEX_ACTION_NAME = {idx: keyword for keyword, idx in ACTION_INDEX_BY_KEYWORD.items()}


class RosActionIndexReceiver:
    """
    ROS action index receiver.

    The index values intentionally match the old UDP voice-keyword path:
    left=4, right=5, wave=7, nod=8, shake=10, start=11, end=12,
    woshou=13, good=14, photo1=15, photo2=16,
    left_front=17, right_front=18, right_back=19.

    When compound direction keywords overlap (e.g. "往左前方走"),
    both left_front(17) and left(4) are published, with compound first.
    """

    def __init__(self, topic: Optional[str] = None):
        self.topic = topic or getattr(config, "ACTION_INDEX_TOPIC", "/action_index")
        self.running = False
        self.sub = None
        self._lock = threading.Lock()
        self._latest_index: Optional[int] = None
        self._latest_ts = 0.0

    def start(self):
        if self.running:
            return
        if not _HAS_ROS1 or rospy is None or Int32 is None:
            raise RuntimeError("未检测到 ROS1 rospy/std_msgs，无法订阅动作 index 话题")

        if not rospy.core.is_initialized():
            rospy.init_node("action_index_receiver", anonymous=True, disable_signals=True)

        self.sub = rospy.Subscriber(
            self.topic,
            Int32,
            self._callback,
            queue_size=10,
            tcp_nodelay=True,
        )
        self.running = True
        rospy.loginfo("[RosActionIndexReceiver] subscribed: %s", self.topic)

    def stop(self):
        self.running = False
        try:
            if self.sub is not None:
                self.sub.unregister()
        except Exception:
            pass
        self.sub = None

    def _callback(self, msg):
        index = int(msg.data)
        action_name = INDEX_ACTION_NAME.get(index, "unknown")
        with self._lock:
            self._latest_index = index
            self._latest_ts = time.time()
        self.handle_action_index(index, action_name)

    def handle_action_index(self, index: int, action_name: str):
        """Override or edit this method to call the real robot action executor."""
        rospy.loginfo(
            "[RosActionIndexReceiver] action index=%d, action=%s",
            index,
            action_name,
        )

    def get_latest_index(self) -> int:
        with self._lock:
            return self._latest_index if self._latest_index is not None else 0

    async def get_latest_index_async(self) -> int:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.get_latest_index)

    def get_state(self):
        with self._lock:
            index = self._latest_index
            ts = self._latest_ts
        return {
            "topic": self.topic,
            "index": index if index is not None else 0,
            "action": INDEX_ACTION_NAME.get(index, None),
            "timestamp": ts,
        }


def main():
    receiver = RosActionIndexReceiver()
    receiver.start()
    try:
        rospy.spin()
    finally:
        receiver.stop()


if __name__ == "__main__":
    main()
