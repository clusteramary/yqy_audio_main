"""Fixed education demo script for the current branch.

Each demo branch keeps this file focused on one scene. The runtime reads
EDUCATION_DEMO_SCENE["steps"] and plays the robot lines through TTS while
publishing action keywords to /action_index at the configured cue points.
"""

EDUCATION_DEMO_SCENE = {
    "id": "demo3_scene3",
    "title": "第三幕：项目与学生能力画像总结",
    "description": "面向大屏和师生总结项目过程、能力画像和后续关注点。",
    "steps": [
        {
            "type": "say",
            "speaker": "机器人",
            "text": "本轮项目讨论已完成总结。小组最初的问题范围较宽，经过两次引导后，已经聚焦为大一新生考试周规划 Agent。",
            "actions_before": ["left"],
            "actions_after": ["nod"],
            "wait_after": 0.4,
        },
        {
            "type": "say",
            "speaker": "机器人",
            "text": "从过程表现看，",
            "actions_before": ["right_front"],
            "wait_after": 1.5,
        },
        {
            "type": "say",
            "speaker": "机器人",
            "text": "学生 A 在需求理解方面贡献较多，",
            "wait_after": 1.5,
        },
        {
            "type": "say",
            "speaker": "机器人",
            "text": "学生 B 在系统决策设计方面表现突出，",
            "wait_after": 1.5,
        },
        {
            "type": "say",
            "speaker": "机器人",
            "text": "学生 C 提出了反馈调整机制。建议教师后续重点关注小组对数据来源和评价指标的完善。",
            "actions_after": ["good"],
            "wait_after": 0.4,
        },
        {
            "type": "say",
            "speaker": "机器人",
            "text": "同学们的思路已经从发散功能，转变为围绕用户、输入、决策和交互持续迭代。请继续完善数据来源和评价指标，加油。",
            "actions_before": ["nod"],
            "actions_after": ["good"],
            "wait_after": 0.8,
        },
    ],
}
