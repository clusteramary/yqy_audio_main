"""Fixed education demo script for the current branch.

Each demo branch keeps this file focused on one scene. The runtime reads
EDUCATION_DEMO_SCENE["steps"] and plays the robot lines through TTS while
publishing action keywords to /action_index at the configured cue points.
"""

EDUCATION_DEMO_SCENE = {
    "id": "demo2_scene2",
    "title": "第二幕：主动介入，收敛项目需求",
    "description": "观察学生发散讨论，主动介入并把项目需求收敛成结构化 Agent 方案。",
    "steps": [
        {
            "type": "say",
            "speaker": "机器人",
            "text": "我注意到你们已经提出了很多功能，但现在有一个关键问题还没有确定：这个 Agent 第一版到底服务谁？",
            "actions_before": ["left"],
            "wait_after": 0.4,
        },
        {
            "type": "pause",
            "speaker": "学生 A",
            "text": "服务所有大学生。",
            "duration": 3.0,
        },
        {
            "type": "say",
            "speaker": "机器人",
            "text": "所有大学生范围太宽。如果第一版只能选择一个具体用户，你们会选择大一新生、考研学生，还是社团负责人？",
            "actions_before": ["right"],
            "wait_after": 0.4,
        },
        {
            "type": "pause",
            "speaker": "学生 B",
            "text": "那我们选大一新生吧。",
            "duration": 3.0,
        },
        {
            "type": "say",
            "speaker": "机器人",
            "text": "好。那大一新生在校园学习生活中，最容易遇到的三个高频问题是什么？请每个人说一个，不要重复。",
            "actions_before": ["nod"],
            "wait_after": 0.4,
        },
        {
            "type": "pause",
            "speaker": "学生 C",
            "text": "我觉得是考试周不知道怎么安排复习。",
            "duration": 3.0,
        },
        {
            "type": "pause",
            "speaker": "学生 A",
            "text": "还有刚入学的时候，对校园资源不熟悉。",
            "duration": 3.0,
        },
        {
            "type": "pause",
            "speaker": "学生 B",
            "text": "还有时间管理不好，学习、社团和休息冲突。",
            "duration": 3.0,
        },
        {
            "type": "say",
            "speaker": "机器人",
            "text": "很好。现在你们的项目对象已经从所有大学生收敛为大一新生，核心场景可以聚焦在考试周规划、校园资源推荐和时间管理冲突上。",
            "actions_before": ["good"],
            "actions_after": ["nod"],
            "wait_after": 0.4,
        },
        {
            "type": "say",
            "speaker": "机器人",
            "text": "现在我们已经确定了用户和场景。接下来需要设计 Agent 架构。第一个问题：这个 Agent 需要哪些输入？",
            "actions_before": ["left"],
            "wait_after": 0.4,
        },
        {
            "type": "pause",
            "speaker": "学生 A",
            "text": "课表、考试时间、自习室开放情况。",
            "duration": 3.0,
        },
        {
            "type": "pause",
            "speaker": "学生 B",
            "text": "还要学生目标，比如这周想重点复习什么。",
            "duration": 3.0,
        },
        {
            "type": "pause",
            "speaker": "学生 C",
            "text": "可以加入疲劳状态，太累的时候提醒休息。",
            "duration": 3.0,
        },
        {
            "type": "say",
            "speaker": "机器人",
            "text": "很好。那它要做什么决策？",
            "actions_before": ["nod"],
            "wait_after": 0.4,
        },
        {
            "type": "pause",
            "speaker": "学生 A",
            "text": "决定哪门课优先复习。",
            "duration": 3.0,
        },
        {
            "type": "pause",
            "speaker": "学生 B",
            "text": "决定什么时候休息，去哪自习。",
            "duration": 3.0,
        },
        {
            "type": "pause",
            "speaker": "学生 C",
            "text": "还可以判断要不要调整健身计划。",
            "duration": 3.0,
        },
        {
            "type": "say",
            "speaker": "机器人",
            "text": "那它的输出应该只是一个计划表吗？",
            "actions_before": ["right"],
            "wait_after": 0.4,
        },
        {
            "type": "pause",
            "speaker": "学生 B",
            "text": "不应该，还要提醒、追问和调整。",
            "duration": 3.0,
        },
        {
            "type": "say",
            "speaker": "机器人",
            "text": "所以你们的 Agent 不是一个静态日程表，而是一个能够持续跟进的学习生活规划助手。它的架构可以分为三层：输入层包括课表、考试、校园资源和学生状态；决策层包括优先级、时间块、地点和调整规则；交互层包括提醒、追问、鼓励和复盘。",
            "actions_before": ["left_front"],
            "actions_after": ["good"],
            "wait_after": 0.8,
        },
    ],
}
