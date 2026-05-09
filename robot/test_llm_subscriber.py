#!/usr/bin/env python3
import json
import rospy
from std_msgs.msg import String


def callback(msg):
    try:
        data = json.loads(msg.data)
    except json.JSONDecodeError as e:
        rospy.logwarn(f"JSON 解析失败: {e}")
        return

    frame_id = data.get("frame_id")
    person_count = data.get("person_count", 0)
    persons = data.get("persons", [])

    print("=" * 60)
    print(f"frame_id: {frame_id}")
    print(f"person_count: {person_count}")

    for person in persons:
        person_id = person.get("person_id")
        bbox = person.get("bbox_pixel", {})
        actions = person.get("actions", [])

        print(f"person_id: {person_id}")
        print(f"bbox: {bbox}")

        action_texts = []
        for action in actions:
            name = action.get("name")
            score = action.get("score")
            action_texts.append(f"{name}({score})")

        print("actions:", ", ".join(action_texts))


def main():
    rospy.init_node("llm_action_subscriber_test", anonymous=True)

    rospy.Subscriber(
        "/perception/yowo2/actions",
        String,
        callback,
        queue_size=10
    )

    print("LLM subscriber started. Waiting for /perception/yowo2/actions ...")
    rospy.spin()


if __name__ == "__main__":
    main()