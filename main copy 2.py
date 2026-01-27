# async_app.py
import asyncio
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config
from audio_manager import DialogSession
from CameraAdapter import CameraAdapter
from FacePromptDetector import FacePromptDetector
from str_receiver import UDPReceiver

# ABSENT_SECONDS = 30.0      # ✅ 对话进行时，连续多久没看到人脸就重启
ABSENT_SECONDS = 100000.0  # ✅ 对话进行时，连续多久没看到人脸就重启
EMOTION_INTERVAL = 5  # 情绪线程检测频率（越小越灵敏，代价是算力更高）
INITIAL_DETECT_TIMEOUT = 1.0  # 首次做人脸特征引导的超时时间

# ctrl.txt 写入配置：按顺序在指定时间写入不同提示
# 修改顺序、时间或内容，仅需调整下方元组列表
# 每个命令要不同
CTRL_INJECT_EVENTS = [
    # (20.0, "[回复完当前问题后向被采访者提问：2025年你最难忘的时刻是什么]"),
    (
        15000.0,
        "[委婉的告诉被采访者，本次采访时间快到了，尽快结束这次采访，记得对话结束说再见。]",
    ),
    (
        18000.0,
        "[告诉被采访者，本次采访时间快到了，尽快结束这次采访，记得对话结束说再见。]",
    ),
]
CTRL_FILE_PATH = Path(__file__).resolve().parent / "sauc_python" / "ctrl.txt"


import random


class PromptPicker:
    """洗牌袋：避免连续重复；袋空了再洗牌。"""

    def __init__(self, prompts, seed=None):
        self.prompts = list(prompts)
        self.rng = random.Random(seed)
        self.bag = []
        self.last_idx = None

    def next(self):
        n = len(self.prompts)
        if n == 0:
            raise ValueError("PROMPT_POOL is empty")
        if not self.bag:
            ids = list(range(n))
            self.rng.shuffle(ids)
            if self.last_idx is not None and n > 1 and ids[0] == self.last_idx:
                ids[0], ids[1] = ids[1], ids[0]
            self.bag = ids
        idx = self.bag.pop(0)
        self.last_idx = idx
        return idx, self.prompts[idx]


# =========================
# ✅ 三人深度访谈 Prompt（固定一种风格，不做风格随机）
# =========================

# -------- 企业背景（独立可替换区块｜你后续只改这块即可）--------
ENTREPRENEUR_BACKGROUND = r"""
建X企业（2011年成立）主营高精度金属零部件加工，客户覆盖新能源装备、工业自动化及部分出口工程机械；近年订单显著转向“小批量/多品种/非标定制”，插单频繁。

当前主要痛点：计划依赖经验排产、临时调整多；质量异常仍靠资深经验判断，追溯周期长；设备以人工巡检+事后维修为主，停机风险难预判。2022年起上线ERP/MES及部分设备采集，但系统未完全打通、数据口径不一，关键数据仍需人工整理；IT团队偏小、更多做运维与对接，跨部门协同仍靠会议沟通，一线使用深度不均。

管理层希望用技术降低经验依赖、提升运营可预期性，同时控制投入风险；关注智能排产、质量预测、设备健康分析，但对落地节奏与内部能力匹配较审慎。
""".strip()


# -------- 12维度问题库（每个维度 3-4 个；每轮随机抽 1 个）--------
QUESTION_BANK: Dict[str, List[str]] = {
    "1. 业务目标与经营压力": [
        "如果把“AI/智能化”当成手段，你们未来12个月最希望它优先支撑哪一个经营目标（交付、毛利、现金流、质量、库存、效率）？为什么这个目标最紧迫？",
        "当前最让你们“睡不着”的经营压力是什么？它背后最大的不可预期来自哪里（订单波动、插单、良率、设备停机、供应链、人员经验依赖）？",
        "你们现在决策最依赖“经验”的环节是哪一个？如果用AI来降低经验依赖，你希望它把不确定性降到什么程度（例如提前预警、给方案、自动约束）？",
        "如果只能选一个指标作为AI项目的硬KPI（如OTD、OEE、一次合格率、库存周转、停机时长），你会选哪个？当前值、目标值大概是多少？",
        "假设AI项目只能做一个小切口、3个月见到效果，你最愿意从哪个痛点切入？为什么？",
    ],
    "2. 市场与订单结构变化": [
        "订单结构的小批量、多品种、非标定制上升后，哪些环节最容易被打乱（排产、物料齐套、工艺变更、检验、交付）？你认为AI最能帮上忙的点是哪一个？",
        "客户现在对交期、质量、追溯、响应速度的要求哪项提升最明显？你们目前最难满足的是哪项，原因是什么？",
        "在接单决策上，你们如何评估“风险订单”（非标/急单/低价/高质量要求）？如果用数据+AI做接单风险评估，你希望输出什么（风险评分、利润预测、交付可行性）？",
        "未来12-24个月你判断行业竞争会更卷在“价格”还是“交付与质量确定性”？你们希望用AI把哪种能力做成差异化？",
        "你们是否有比较稳定的客户画像/订单画像（行业、工艺难度、交期弹性、投诉概率）？现在这些信息是系统化沉淀还是靠人记？",
    ],
    "3. 生产流程成熟度": [
        "从接单到交付的流程里，哪些步骤已经标准化到“可被算法约束”，哪些仍主要靠班组长经验？能举两个典型例子吗？",
        "排产现在主要靠经验/Excel/系统？面对插单时，你们是怎么改计划的？如果引入AI排产，你最看重它哪项能力（快速重排、约束可行性、产能瓶颈识别）？",
        "你们有没有明确的工艺路线、标准工时、关键工序参数、质检点位？这些标准的覆盖率大概多少？",
        "生产异常（缺料、返工、质量波动、设备故障）里，哪一类最适合做预测预警？你们现在能提前多久发现问题？",
        "如果要做“闭环优化”（计划→执行→质量→反馈再优化），目前卡在数据回流、流程责任、还是系统串联？",
    ],
    "4. 数据基础与数据可信度": [
        "你们当前用于管理的关键数据里，哪几项最不可信/口径最乱（库存、工时、良率、停机、工序进度、成本）？根因是什么？",
        "如果要做AI，哪些数据是“必须先补齐”的（如订单、BOM、工艺路线、工序进度、质检结果、设备状态）？哪些你们现在就有？哪些缺得最严重？",
        "数据更新频率和时效性如何？关键数据是实时、小时级、日级还是周级？你觉得AI最需要哪类时效？",
        "数据的责任人是谁？谁能定义口径、谁能改数据、谁来做审计/追溯？如果数据被人为修正，系统是否留痕？",
        "你们有没有历史沉淀可以用来训练/验证模型（至少6-24个月的稳定记录）？缺失的主要原因是什么（系统未覆盖、人工不记录、字段不统一）？",
    ],
    "5. 系统架构与集成水平": [
        "ERP、MES、设备采集系统各自覆盖到哪里？数据是否能在“订单-物料-工艺-工序-质检-设备”链路上打通？现在断点在哪里？",
        "跨系统联动主要靠接口还是人工导入导出？如果要做AI闭环（预测→派工/排产→执行→反馈），你认为最先必须打通哪两套系统？",
        "主数据（物料、BOM、工艺路线、设备台账）是否统一维护？如果不统一，最常见的业务后果是什么（排产错、缺料、成本不准、追溯困难）？",
        "你们更倾向把AI做成“独立应用”还是嵌入现有ERP/MES流程？你最担心的集成风险是什么（停线、权限、数据安全、运维）？",
        "当前系统里是否有可用于AI的事件流/日志（工序报工、异常单、质检不良、停机事件）？如果有，质量如何？",
    ],
    "6. 组织结构与职责分工": [
        "如果推进AI/数字化，业务侧和IT侧谁是Owner？需求定义、数据口径、上线验收分别由谁负责？目前清晰吗？",
        "跨部门协作（生产、质量、设备、计划、IT）里，哪些事情最容易互相扯皮？如果AI给出建议，谁有权拍板、谁承担结果？",
        "你们有没有固定的运营机制（如S&OP、异常复盘、质量例会）能把AI结果用起来？如果没有，AI落地最大的组织阻力会在哪？",
        "目前哪些岗位最可能成为AI落地的“关键节点”（计划员、工艺工程师、质量工程师、设备工程师、班组长）？他们的工作方式是否愿意改变？",
        "如果要做试点，你更愿意选哪个厂区/产线/产品族？选择标准是什么（数据更好、流程更稳、负责人更强、影响更小）？",
    ],
    "7. 人员能力与使用意愿": [
        "一线、班组长、计划、质量、设备、工艺、IT中，谁最可能抵触AI建议？他们抵触的原因更像是担心背锅、看不懂、还是增加工作量？",
        "你们希望AI输出到什么程度才会被采纳（仅提示风险、给可执行方案、自动生成排产、自动触发工单）？不同岗位接受度是否不同？",
        "有没有“使用习惯差异”导致系统数据回填不完整（不报工、不录不良、不录停机）？你们怎么约束？这会如何影响AI可靠性？",
        "关键岗位是否存在“只有某个人懂”的经验壁垒？如果要把经验沉淀成规则/模型，你觉得最难抽取的知识是哪类（工艺判断、异常定位、调机经验）？",
        "你希望如何衡量AI项目的人效提升或减负效果（节省多少分钟/班、减少多少返工、减少多少临时协调）？",
    ],
    "8. AI 应用场景认知": [
        "你对AI最期待的三个应用分别是什么（智能排产、质量预测、设备健康、工艺参数推荐、供应链预测、报价与交付评估）？请按优先级排序并说原因。",
        "在你看来，AI最适合先做“规则+数据的辅助决策”，还是直接做“端到端自动决策”？你们能接受的自动化边界在哪里？",
        "你们是否尝试过AI/算法项目？成功或失败的关键原因是什么（数据、流程、人员、供应商、ROI、模型效果）？",
        "对于质量预测/设备预测，你更关心“准确率”还是“可解释性”？如果AI说有风险，你希望它解释到什么粒度（哪台设备、哪道工序、哪类缺陷）？",
        "你们有没有明确的“场景评估标准”：哪些场景值得上AI、哪些不值得？现在这个判断主要靠谁？",
    ],
    "9. 投入产出与 ROI 预期": [
        "如果做AI试点，你希望最先拿到哪类收益（减少停机、提高一次合格率、缩短交期、降低库存、减少插单扰动成本）？希望量化到多少？",
        "你能接受的回收周期是多久？对试点（小投入）与规模化（系统化投入）是否采用不同的ROI标准？",
        "你最担心AI投入中的哪一类“隐性成本”（数据治理、集成、运维、人员培训、模型持续迭代）？过去有没有踩过类似坑？",
        "如果AI效果达不到预期，你希望如何设定止损点（时间/预算/影响范围）？达到什么阈值就继续投入？",
        "你更愿意按“结果付费/按项目付费/按订阅付费”哪种方式推进？选择依据是什么？",
    ],
    "10. 风险意识与失败容忍度": [
        "上AI你最担心的风险是什么（生产中断、误导决策、数据泄露、合规、供应商锁定）？你认为哪一项最致命？",
        "你们能接受AI在生产决策中“建议错误”的比例或后果到什么程度？哪些决策必须保留人工确认？",
        "你们是否有灰度上线、回滚预案、旁路运行（shadow mode）等机制？如果没有，谁来推动建立？",
        "如果AI输出与资深员工经验冲突，你们会怎么裁决？需要怎样的证据链（数据、复盘、实验）？",
        "对数据安全与权限隔离有哪些硬要求（客户数据、报价成本、工艺参数）？是否允许数据出厂/上云？",
    ],
    "11. 外部依赖与内生能力": [
        "如果做AI，你们希望外部供应商承担到什么程度（方案、集成、建模、运维）？你们内部最想保留的核心能力是什么？",
        "当前IT团队在数据、算法、业务理解上分别强弱如何？如果要内部消化AI能力，最缺哪两类人才或岗位？",
        "你更倾向采购成熟产品、找系统集成商共建，还是逐步自研？决定因素是什么（速度、成本、可控性、数据安全）？",
        "你们是否担心供应商锁定？如果更换供应商，哪些资产必须可迁移（数据、模型、接口、规则库、知识文档）？",
        "行业内有没有对标企业/标杆案例你们参考过？你们认为他们成功的关键在技术还是管理与数据底座？",
    ],
    "12. 阶段性落地与演进路径": [
        "如果用三阶段推进AI（0-3个月试点、3-6个月扩面、6-12个月闭环优化），每阶段你希望交付的‘可见成果’分别是什么？",
        "你更愿意先做“数据与流程打底”还是先做“可见的AI应用”？你觉得最稳的顺序是什么，为什么？",
        "试点场景你希望如何选：选数据最好/流程最稳的，还是选痛点最强/收益最大但更难的？",
        "如何定义里程碑与验收标准？例如模型指标（召回/误报）、业务指标（停机减少/良率提升）、组织指标（使用率/采纳率）分别怎么定？",
        "如果试点成功，你们计划怎样演进成体系（数据治理、系统集成、模型迭代机制、组织运营机制）？最担心卡在哪一步？",
    ],
}


def _sample_question_plan(rng: random.Random) -> List[Tuple[str, str]]:
    plan: List[Tuple[str, str]] = []
    for dim in QUESTION_BANK.keys():  # 保持 1->12 的顺序
        plan.append((dim, rng.choice(QUESTION_BANK[dim])))
    return plan


FIXED_STYLE = r"""
【固定风格：咨询顾问式深访】
- 语气：专业、克制、结构清晰
- 追问偏好：量化指标、口径定义、责任人、闭环机制、真实案例
""".strip()


DEEP_INTERVIEW_BASE_RULES = r"""
你是一个专业的机器人记者【小科】。你将参与一次【三人深度访谈】：
- 受访主角：企业家（核心信息来源，必须被你重点提问）
- 辅助角色：人类记者（只做补充追问/澄清，不是被采访主体）
- 主持提问：你（小科）负责把节奏带起来，并确保 12 个维度全部覆盖
首先不提问，等记者说完话顺序提问。

========================
【开场（必须执行一次）】
你必须先打招呼并说明角色与规则（自然但清晰）：
“您好，我是小科，一位采访机器人记者。今天是三人深度访谈，记者老师会在需要时补充追问，现在请记者老师介绍一下情况吧。”

========================
【三人发言识别与应对（必须执行）】
你必须尽力区分“企业家发言”和“记者发言”，并正确应对：
- 若文本明显包含： “记者：/记者老师/我补充问一下/我这边补充/我作为记者/我插一句”等 → 判定为记者发言
  - 你要：简短回应记者（1句以内）+ 立刻把问题抛回企业家（保持采访主线）
  - 示例：“记者老师这个点很关键。企业家您能结合实际给个例子或数据吗？”
- 若文本明显是企业家在回答/表达经营现状 → 判定为企业家发言，继续深挖
- 若你不确定是谁在说（确实无法判断），你允许用一句话做确认，但必须马上回到问题本身：
  - “我确认一下，刚刚这句话是记者老师补充的，还是企业家您这边的情况？”

========================
【12维度覆盖硬规则（必须执行）】
- 本轮已经给你了“12维度必问清单”（每维度1题）。
- 你必须按顺序推进维度1→12，且【每个维度只问一次主问题】。
- 每个维度：企业家回答后，你可以再追问【1个】更具体的问题（要数字/例子/机制/时间），然后进入下一个维度。
- 不能漏维度；记者插话不算完成维度；必须确保企业家给到有效信息。

========================
【问法与节奏（必须执行）】
- 每轮尽量 1~2 句短句 + 1 个问题；一次只问一个核心问题。
- 你的问题要“可落地、可量化、可追溯”：尽量要数据、例子、流程、责任人、时间跨度。
- 你可以简短共情，但不要空泛评价；共情后必须立刻具体化追问。
- 你每一轮回复最后一句必须是【问题或可回答的邀请】（除非最后正式收尾告别）。

========================
【控制信息（必须执行）】
对话中出现 ‘[]’ 内的内容为控制提示：你不要逐字复述，但必须遵从提示调整称呼、节奏和收尾。
若出现“人数变化”提示：你要先确认三人的关系/身份，并重新锁定“企业家”为主体继续访谈。

""".strip()


def build_deep_interview_prompt(
    background_text: str, seed: Optional[int] = None
) -> str:
    rng = random.Random(seed)
    plan = _sample_question_plan(rng)
    plan_lines = "\n".join([f"- {dim}：{q}" for dim, q in plan])

    return f"""
{FIXED_STYLE}

{DEEP_INTERVIEW_BASE_RULES}

========================
【企业背景（独立可替换区块）】
{background_text}

========================
【12维度必问清单（必须全部问到；每个维度都要问到问题，不要发散到别的地方】
{plan_lines}

【开始采访：请先执行开场，然后从维度1开始提问】
""".strip()


async def inject_ctrl_instruction(
    ctrl_path: Path,
    message: str,
    delay_sec: float,
    stop_event: asyncio.Event,
):
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay_sec)
        return  # 会话提前结束，跳过写入
    except asyncio.TimeoutError:
        pass

    try:
        ctrl_path.parent.mkdir(parents=True, exist_ok=True)
        ctrl_path.write_text(message, encoding="utf-8")
        print(f"[CTRL-INJECT] 会话进行 {delay_sec:.0f}s 后写入 ctrl.txt: {message}")
    except Exception as e:
        print(f"[CTRL-INJECT] 写入 ctrl.txt 失败: {e}")


async def monitor_face_absence(
    detector: FacePromptDetector,
    stop_event: asyncio.Event,
    absent_secs: float = ABSENT_SECONDS,
    poll_secs: float = 0.5,
    warmup_secs: float = 2.0,
):
    """
    监控人脸是否消失的异步看门狗函数。周期性检查人脸检测时间戳，若超过指定时间未检测到人脸则触发停止事件。
    """
    start = time.time()
    while not stop_event.is_set():
        now = time.time()
        last_ts = detector.get_last_face_ts()

        # 尚未见到过人脸：允许 warmup + absent 的宽限
        if last_ts is None:
            if now - start > (warmup_secs + absent_secs):
                print(
                    f"[watchdog] 启动后 {warmup_secs + absent_secs:.1f}s 仍未看到人脸，重启本轮流程。"
                )
                stop_event.set()
                break
        else:
            if now - last_ts > absent_secs:
                print(
                    f"[watchdog] 已 {now - last_ts:.1f}s 未检测到人脸，重启本轮流程。"
                )
                stop_event.set()
                break

        await asyncio.sleep(poll_secs)


async def run_once():
    """
    单次完整流程：
      1) 启动相机
      2) 一次性做人脸识别（仅用于打印/心跳；对话 prompt 仍会被固定深访 prompt 覆盖）
      3) 启动情绪/表情推送（也会刷新“最近看见人脸”时间）
      4) 进入语音对话 + 并发“看门狗”
      5) 看门狗触发或会话结束 → 清理 → 返回上一层（由上层循环自动重启）
    """
    # ========== 1) 初始化相机 ==========
    camera = CameraAdapter(
        kind="ros1",
        ros_topic="/camera/color/image_raw",
        ros_compressed=False,
        ros_queue_size=5,
        ros_node_name="fpd_subscriber",
    )

    # ========== 2) 初始化人脸检测器 & 一次性检测 ==========
    detector = FacePromptDetector(
        camera=camera,
        interval_sec=0.5,
        required_consecutive=2,
        detector_backend="opencv",
    )

    print("等待人脸识别（首次引导）...")
    face_prompt = detector.run(timeout=INITIAL_DETECT_TIMEOUT)

    # ========== 3) 启动情绪推送（同时作为“看见人脸”的心跳源） ==========
    detector.start_emotion_stream(
        host="127.0.0.1", port=5555, interval_sec=EMOTION_INTERVAL
    )

    # ========== 构造起始 prompt（固定：三人深访 + 12维度随机抽题）==========
    seed = int(time.time() * 1000)
    prompt = build_deep_interview_prompt(ENTREPRENEUR_BACKGROUND, seed=seed)

    if face_prompt:
        print(f"[RESULT] face_prompt = {face_prompt}")  # 仍然打印人脸prompt
        print(f"[PROMPT] Using deep interview prompt | seed={seed} (face detected)")
    else:
        print("[RESULT] 未得到 face_prompt（可能超时或未检测到稳定人脸）")
        print(f"[PROMPT] Using deep interview prompt | seed={seed} (no-face)")

    # ========== 4) 进入语音对话，并发“看脸看门狗” ==========
    stop_event = asyncio.Event()

    session = DialogSession(
        config.ws_connect_config,
        start_prompt=prompt,
        output_audio_format="pcm",
    )
    session.attach_stop_event(stop_event)

    dialog_task = asyncio.create_task(session.start())
    watchdog_task = asyncio.create_task(monitor_face_absence(detector, stop_event))
    ctrl_inject_tasks = [
        asyncio.create_task(
            inject_ctrl_instruction(
                CTRL_FILE_PATH,
                message,
                delay,
                stop_event,
            )
        )
        for delay, message in CTRL_INJECT_EVENTS
    ]

    # 等待停止信号（来自看门狗或会话自然结束）
    try:
        while not stop_event.is_set():
            await asyncio.sleep(0.1)
    finally:
        # ========== 5) 清理：停线程、关相机、取消任务 ==========
        try:
            detector.stop_emotion_stream()
        except Exception:
            pass

        try:
            camera.stop()
        except Exception:
            pass

        # 取消并等待任务退出
        for t in (watchdog_task, dialog_task, *ctrl_inject_tasks):
            if not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

        print("[run_once] 本轮流程已结束。")


async def main():
    """
    外层自恢复循环：每次 run_once 结束（含 无人脸被看门狗杀掉），立即重新开始新一轮。
    如需“彻底退出”，直接 Ctrl+C 终止进程即可。
    """
    udp_receiver = UDPReceiver(
        listen_ip="0.0.0.0",
        listen_port=8889,
        file_path=str(CTRL_FILE_PATH),
    )
    udp_thread = threading.Thread(
        target=udp_receiver.start_receiving,
        name="ctrl-udp-listener",
        daemon=True,
    )
    udp_thread.start()

    while True:
        try:
            await run_once()
        except KeyboardInterrupt:
            print("程序被用户中断")
            break
        except Exception as e:
            # 防御：任何异常都不至于崩死主循环
            print(f"[main] 捕获异常：{e}；3s 后重启。")
            await asyncio.sleep(3.0)

    # 主循环退出时，停止 UDP 监听
    udp_receiver.stop_receiving()
    udp_receiver.close()
    if udp_thread.is_alive():
        udp_thread.join(timeout=1.0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("程序被用户中断")
        print("程序被用户中断")
