#!/usr/bin/env python3
"""从 ROS1 话题抓一张图片并保存到本地"""
import sys
import os

sys.path.insert(0, "/opt/ros/noetic/lib/python3/dist-packages")

import rospy
import numpy as np
from sensor_msgs.msg import CompressedImage, Image
from cv_bridge import CvBridge
import cv2

SAVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ros_grabbed.jpg")

TOPIC = "/camera/color/image_raw"
TIMEOUT_SEC = 10

bridge = CvBridge()
got_frame = False


def image_cb(msg):
    global got_frame
    try:
        img = bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
    except Exception:
        img = bridge.imgmsg_to_cv2(msg)
        if img.ndim == 3 and img.shape[2] == 3:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

    cv2.imwrite(SAVE_PATH, img)
    h, w = img.shape[:2]
    print(f"[OK] 保存图片: {SAVE_PATH}  ({w}x{h})")
    got_frame = True


def compressed_cb(msg):
    global got_frame
    np_arr = np.frombuffer(msg.data, np.uint8)
    img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    cv2.imwrite(SAVE_PATH, img)
    h, w = img.shape[:2]
    print(f"[OK] 保存图片: {SAVE_PATH}  ({w}x{h})")
    got_frame = True


if __name__ == "__main__":
    rospy.init_node("grab_one_image", anonymous=True, disable_signals=True)

    # 先探测话题类型
    pub_info = None
    try:
        pub_info = rospy.get_published_topics()
    except Exception:
        pass

    # 判断是否有 /compressed 版本
    topic = TOPIC
    use_compressed = False
    for t, _ in (pub_info or []):
        if t == TOPIC + "/compressed":
            use_compressed = True
            topic = t
            break

    if use_compressed:
        sub = rospy.Subscriber(topic, CompressedImage, compressed_cb, queue_size=1)
        print(f"[*] 订阅 CompressedImage: {topic}")
    else:
        sub = rospy.Subscriber(topic, Image, image_cb, queue_size=1)
        print(f"[*] 订阅 Image: {topic}")

    print(f"[*] 等待图片 (最多 {TIMEOUT_SEC}s) ...")
    start = rospy.get_time()
    while not got_frame and (rospy.get_time() - start) < TIMEOUT_SEC and not rospy.is_shutdown():
        rospy.sleep(0.1)

    if not got_frame:
        print(f"[FAIL] {TIMEOUT_SEC}s 内未收到图片，请检查话题是否有数据发布:")
        print(f"       rostopic hz {TOPIC}")
        sys.exit(1)

    sub.unregister()
    print("[DONE]")
