"""Fixed education demo script for the current branch.

Each demo branch keeps this file focused on one scene. The runtime reads
EDUCATION_DEMO_SCENE["steps"] and plays the robot lines through TTS while
publishing action keywords to /action_index at the configured cue points.
"""

EDUCATION_DEMO_SCENE = {
    "id": "demo1_scene1",
    "title": "第一幕：项目启动与任务答疑",
    "description": "项目启动、流程说明、评审规则解释，并主动发现学生 A 的疑惑。",
    "steps": [
        {
            "type": "say",
            "speaker": "机器人",
            "text": "同学们好，我是你们本次项目制学习的机器人导师。今天我们要完成一个真实的智能体设计任务：为大一新生设计一个校园学习生活规划 Agent。",
            "actions_before": ["wave"],
            "wait_after": 0.4,
        },
        {
            "type": "say",
            "speaker": "机器人",
            "text": "本项目分为三个阶段：第一，明确用户和需求；第二，设计 Agent 的输入、决策和交互方式；第三，完成方案展示和评审。",
            "actions_before": ["left"],
            "wait_after": 1,
        },
        {
            "type": "say",
            "speaker": "机器人",
            "text": "评审时，我会重点关注四点：用户是否具体、需求是否真实、架构是否清晰、方案是否能够持续优化。",
            "actions_before": ["right"],
            "actions_after": ["nod"],
            "wait_after": 1.4,
        },
        {
            "type": "say",
            "speaker": "机器人",
            "text": "我注意到这位刚才在评分规则这里停留了比较久，好像有些疑惑。你是不是想确认，需求是否真实这一项具体怎么判断？",
            "actions_before": ["right_front"],
            "wait_after": 0.4,
        },
        {
            "type": "pause",
            "speaker": "学生 A",
            "text": "对，我不太明白。我们是不是只要功能做得多，就能说明项目比较完整？",
            "duration": 5.0,
        },
        {
            "type": "say",
            "speaker": "机器人",
            "text": "功能多不一定代表项目好。这个项目更看重的是：用户对象是否明确，使用场景是否具体，问题是否真实存在。也就是说，你们后面设计功能时，要先说明这些功能为什么需要，而不是一开始就堆很多功能。",
            "actions_before": ["left"],
            "actions_after": ["nod"],
            "wait_after": 0.8,
        },
    ],
}
