# 服务器 Codex 交接与实验执行说明

交接日期：2026-10-22。仓库：<https://github.com/clusteramary/yqy_audio_main>，工作分支：`biyelunwen`。

这是硕士毕业论文项目的服务器交接入口。请完整阅读后实际检查环境、运行验证并逐步开始实验。此前开发在 Windows + GTX 1060 上完成，主要使用 CPU；本次交接前没有启动真实数据正式训练，没有训练结果或真人用户实验结论。模型前向检查与软件测试不等于科研有效性。

## 1. 项目前因后果与论文目标

项目是智能交互机器人的软件部分。原有机器人实时语音、播放、ROS 和动作索引接口保留在 `robot_runtime/`；毕业论文研究实现位于 `interview_robot/`。暂定题目为《面向智能交互机器人的鲁棒多模态情感感知与知识增强追问方法研究》。三个研究部分为：

1. **点一：句末多模态情感感知。** 使用完整话语结束后的文本、音频、视觉与此前历史，研究缺失模态、质量代理失准下的分类与置信度可靠性。当前不预测说话中途的轮次结束，也不推断用户真实内心。原设想的交互状态范围已收窄到能由公开标签支持的任务。
2. **点二：历史覆盖与知识增强追问排序。** 给定已经发生的历史 H、仅由历史检索的知识 K、固定候选 C，对候选追问重排。当前不是开放式生成或全库召回。需要检验知识收益、非重复性与无关知识下的退化。
3. **点三：机器人软件系统与闭环验证。** 研究观测、模型建议、请求、播放确认与动作反馈的完整过程。机器人本体是载体；不能仅凭硬件存在声称第三个原创算法。

用户还提供过专利《一种多模态情感语言交互与动作规划决策方法及系统》，用于说明已有工作背景。代码、专利技术方案与真实实验成果必须分别举证；不能将专利提到但仓库未实现的 VITS 等模块写成已完成研究。

## 2. 克隆与 Git LFS：先下载实物文件

```bash
git lfs install
GIT_LFS_SKIP_SMUDGE=1 git clone --branch biyelunwen --single-branch https://github.com/clusteramary/yqy_audio_main.git
cd yqy_audio_main
git lfs pull
python3 scripts/restore_server_assets.py
git status --short --branch
git rev-parse HEAD
```

初次 clone 使用 skip-smudge 可以先获得代码并单独观察 LFS 下载进度；**必须完成 `git lfs pull` 和上述权重恢复命令才能训练**。视频、模型权重和本次大 JSON/JSONL 数据包含 LFS 指针。指针文件的存在不代表内容已下载；不能把指针文本当数据、视频或权重。

中文BERT原始权重411,553,788字节，因单连接上传缓慢且发生中断，使用13个最多32MiB的LFS分片迁移。`models/pretrained/followup/bert-base-chinese/transport.json`记录顺序和各片/完整SHA256；恢复脚本先校验分片，再原子生成原始`model.safetensors`。完整SHA256为`3404a1ffd8da507042e8161013ba2a4fc49858b4e3f8fbf5ce5724f94883aec3`。已有正确权重时跳过，发现不同的已有文件或中断临时文件时保留现场并报错，不覆盖。其余已上传MELD模型仍直接由LFS取得。

已有 MELD 解压视频及三个冻结编码器约十余 GB，加上本次追问数据和中文编码器；请确认网络与磁盘。若 LFS 下载因网络或账号配额失败，保留错误，检查已下载文件，优先使用固定来源下载脚本恢复缺项，不擅自换数据版本。不要删除数据目录来“重来”。

**这次同步包括：**全部当前代码、配置、论文文档、标注材料、审计证据，FQ-Bank/KdConv 固定快照及许可证，原始清理版与 v2 mixed/random 处理数据，中文 BERT 权重和新增离线输出。`docs/server_sync_assets_2026-10-22.json` 给出本次追加资产的精确路径、大小与 SHA256，执行：

```bash
python scripts/restore_server_assets.py
python scripts/verify_server_assets.py
```

虚拟环境、上游 clone 的嵌套 `.git`、下载缓存/未完成分片和重复的 MELD 压缩归档没有重复提交。MELD 解压视频已在仓库 LFS 中，运行特征提取不需要再次下载十余 GB 的归档。所有来源内容仍适用各自许可证。

`.gitignore` 对 raw/processed/models/outputs 的规则只影响尚未跟踪的新文件；已有跟踪资产和本次显式加入的资产会随 clone 迁移。不要看到忽略规则就断言数据均未上传。

## 3. 优先阅读顺序

1. 本文件；`README.md`。
2. `docs/硕士论文研究与迭代规划.md`、`docs/本地持续开发交付_2026-10-08.md`。
3. `docs/论文结构与方法草稿.md`、`docs/论文_01_绪论与问题定义.md`。
4. `docs/论文_实验协议与结果占位.md`、`docs/论文_系统接口与闭环验证方案.md`。
5. `创新点一_数据与训练.md`、`创新点二_数据与训练.md`。
6. 标注手册、AI初标与本文件的数据风险说明。

旧的 `docs/服务器迁移与训练.md` 和早期实验记录包含历史状态、合成样例与旧种子。冲突时以本交接、当前实际代码和2026-10-08后的研究协议为准。正式实验预设种子为 **42、2026、3407**。

## 4. 已完成与未完成

| 模块 | 当前实现 | 主要入口 | 实验状态 |
| --- | --- | --- | --- |
| 点一数据 | MELD 媒体、官方标签、场景去重及已知坏片处理 | `scripts/prepare_meld.py`、`scripts/meld_status.py` | 原始数据已准备，正式特征未生成 |
| 点一模型 | 冻结文本/音频/全帧视觉，质量门控、模态注意力、单向GRU、双任务头及消融 | `meld_features.py`、`meld_training.py`、`scripts/train_meld.py` | 前向/单元测试通过，无正式训练 |
| 点一可靠性 | NLL/Brier/ECE/风险覆盖；q置一、反转、当前句模态错配评测 | evaluate的`--quality-stress` | 元数据压力入口已实现，无效果结论 |
| 点二数据 | FQ-Bank 清理、KdConv弱监督、v2候选、历史限定检索 | `prepare_followup.py`、`prepare_followup_v2.py` | 数据可用，人工金标准未完成 |
| 点二轻量模型 | 冻结句向量、历史位置、双路注意力/门控、词汇覆盖 | `scripts/followup.py` | 全量缓存和训练未做 |
| 点二强基线 | history-only Cross-Encoder，联合编码与listwise优化、dev选模、评测/适配 | `scripts/cross_encoder.py` | 中文BERT和英文DistilBERT真实权重前向通过，分类头未训练 |
| 标注 | 6段60候选AI初标，50段500候选盲包，五维一致性工具 | `scripts/audit_annotation.py` | 人工字段为空，无双人独立结果 |
| 系统 | 原规则控制器；新增独立请求确认、重复/乱序处理、开始/结束超时与人工恢复 | `confirmed_runtime.py`、`replay_confirmed.py` | 已做纯离线故障回放，未实机联调 |
| 论文 | 绪论、任务、方法公式、实验表和系统方案；基础BibTeX | `docs/` | 正文草稿，正式结果全部待填 |

本地2026-10-22重新运行 `python -m pytest tests -q`，结果 **83 passed，6条Transformers FutureWarning**，其中新增分片恢复、幂等性和损坏拒绝检查。请在服务器重新执行并记录自己的结果，不直接复制本地结论作为服务器验证。

## 5. 数据与关键风险

### 5.1 点一 MELD

路径：`data/raw/meld/`、`data/processed/meld/`。模型：`models/pretrained/meld/`。正式特征目标：`data/features/meld-v1/`。

去重后训练/验证/测试保留 9977 / 1109 / 2610 条；以当前 protocol/audit 文件和读取结果复核。已知 dev `110:7` 缺片，train `125:3` 坏片；不将这些当作新增服务器故障，也不能忽略其他失败。训练对异常模态比例有检查，不绕过检查让实验退化为隐式单模态。

`meld_status.py` 的 raw_data_ready 包含“原始大归档存在”条件，服务器只拉取解压视频时可能显示 false。**先区分归档未保留与视频/索引缺失**；检查逐样本媒体和后续特征 preflight，不为追求一个状态布尔值重下重复归档。

当前 q 是静音、削波、截断、图像对比度等启发式信号，不能称校准概率。缓存 age 恒为零；没有时延鲁棒性证据。视觉是全帧 ResNet18，不是说话人面部定位。质量元数据干预不改变音视频内容，不能称真实噪声实验。强相关多模态基线仍待选择公平复现版本。

### 5.2 点二数据

| 数据 | 正式开发路径 | train/dev/test |
| --- | --- | --- |
| FQ-Bank清理版 | `data/processed/followup/fq-bank` | 13293 / 1372 / 2090 |
| KdConv v2 mixed | `data/processed/followup-v2-mixed/kdconv` | 29488 / 3767 / 3818 |
| KdConv v2 random | `data/processed/followup-v2-random/kdconv` | 同上 |
| KdConv v1 | `data/processed/followup/kdconv` | 仅历史审计，禁止据此得正式方法结论 |

v1 的负例全部来自 train，导致不看历史的“未见候选优先”规则在 dev 的 MRR 约0.927。v2使用同划分、同领域、其他对话的负例，消除这一明显来源捷径；mixed最多4个词汇难例加其余随机，random不含难例。dev/test池只用于构造固定离线题目，不用于训练拟合或知识检索。不要把修复前后诊断分数称作模型改进。

**v2依然是弱监督。** 观察到的下一句不保证唯一合理、不保证真是追问，采样负例也可能合适。训练/dev规则预警分别为18207/294880、2680/37670次候选出现；它们是重叠的规则预警，不能当作错误率。候选混入普通回复和知识实体错配尚需修订协议。

建议立即开展 FQ-Bank 与 MELD 的基线排错。KdConv 可用于明确标注为 exploratory/weak-label 的探索实验；正式知识收益、非重复性、追问深度结论必须等待审计口径冻结和人工证据。发现阻断不应让其他独立实验停工。

标注目录 `data/annotations/`：AI初标不能冒充真人金标准；盲标包50段与AI初标对话不重叠。先由真实标注者独立填写、再用一致性工具检查，不要求服务器Codex生成两个“真人”身份。

## 6. 服务器环境与第一次验收

先记录 `nvidia-smi`、Python、驱动、GPU显存、CPU/RAM、磁盘、仓库SHA、工作区修改和LFS状态。建立 Linux 独立环境，推荐沿用项目支持的 Python3.10/3.11；不要复制 Windows `.venv` 或安装机器人旧环境全集。

```bash
python3 -m venv .venv-server
source .venv-server/bin/activate
python -m pip install --upgrade pip
# 先根据本机驱动和PyTorch官方兼容信息安装匹配的torch/torchvision；不要修改驱动。
python -m pip install -r requirements-meld.txt -r requirements-followup.txt
python -m pip install -e .
python -m pytest tests -q
python scripts/restore_server_assets.py
python scripts/verify_server_assets.py
python scripts/followup_status.py
python scripts/meld_status.py --output outputs/server-bootstrap/meld-status.json
python scripts/cross_encoder.py preflight --config configs/cross_encoder_fqbank.json --forward --output outputs/server-bootstrap/cross-fq.json
python scripts/cross_encoder.py preflight --config configs/cross_encoder_kdconv.json --forward --output outputs/server-bootstrap/cross-kd.json
```

torch/torchvision安装步骤需由服务器Codex基于实际GPU选择；requirements固定了 numpy1.26.4、transformers4.44.2 等参考依赖。不要执行仓库根级 `pytest`，应指定 `tests`，以免采集旧机器人/硬件脚本。预检查的随机分类头没有科研分数意义。

检查 `.venv-server/` 等新环境目录被本地忽略；不要提交虚拟环境、凭据或超大新缓存。已有公开数据按仓库策略管理，正式结果通过清晰的小报告与索引追踪。

## 7. 点一执行顺序

先从真实片段做少量GPU提取，使用独立debug目录；成功后做全量冻结特征并检查失败清单：

```bash
python scripts/meld_features.py extract --device cuda --splits dev --limit 4 --output data/features/meld-server-debug
python scripts/meld_features.py extract --device cuda
python scripts/train_meld.py preflight --forward --output outputs/server-bootstrap/meld-preflight.json
python scripts/train_meld.py train --variant text_only --seed 42 --device cuda --output outputs/meld/text_only-seed42
python scripts/train_meld.py train --variant full --seed 42 --device cuda --output outputs/meld/full-seed42
python scripts/train_meld.py evaluate --checkpoint outputs/meld/full-seed42/best.pt --split dev --device cuda --quality-stress --output outputs/meld/full-seed42/dev-quality-v1
```

这是单种子验证开发流程，参数/协议修改后不得混入同一正式表。之后逐项扩展9种variant和三个种子，视计算预算先完成核心对照。训练按dev emotion Macro-F1选模。质量压力开关增加3项元数据条件，连同原8种条件共11项；不加开关保持原行为。

全量特征读取train/dev/test媒体进行冻结编码不等于用test标签训练；统计归一化只拟合保留的train。正式调参不读取test指标。明确模型选择规则后才显式 `--split test`，输出使用全新目录。

## 8. 点二轻量模型与强基线

FQ-Bank特征及单种子训练：

```bash
python scripts/followup.py cache --input data/processed/followup/fq-bank/train.jsonl data/processed/followup/fq-bank/dev.jsonl data/processed/followup/fq-bank/test.jsonl --encoder models/pretrained/meld/distilbert-base-uncased --output data/features/followup/fq-bank --device cuda
python scripts/followup.py train --config configs/followup_fqbank.json --seed 42 --device cuda --output outputs/followup/fq-bank/history-42
python scripts/followup.py evaluate --checkpoint outputs/followup/fq-bank/history-42/best.pt --input data/processed/followup/fq-bank/dev.jsonl --cache data/features/followup/fq-bank --output outputs/followup/fq-bank/history-42/dev.json --device cuda
python scripts/cross_encoder.py train --config configs/cross_encoder_fqbank.json --seed 42 --device cuda --output outputs/cross-fqbank/seed42
python scripts/cross_encoder.py evaluate --checkpoint outputs/cross-fqbank/seed42/best --input data/processed/followup/fq-bank/dev.jsonl --output outputs/cross-fqbank/seed42/dev --device cuda
```

`configs/cross_encoder_kdconv.json` 使用中文BERT，`cross_encoder_fqbank.json` 使用英文DistilBERT。联合最大长度512，问题前96 tokens，历史保留最近8句并从末尾截断，batch_size=1指一组候选，不是一对文本。使用listwise分级多正例损失，dev MRR选最佳。它与冻结模型每句128tokens的预算不同，要统计截断与计算预算，不直接声称完全相同token输入或复现原论文表。

KdConv探索用当前v2 mixed，缓存命令将输入目录换为 `data/processed/followup-v2-mixed/kdconv/`、编码器换为 `models/pretrained/followup/bert-base-chinese`、缓存目标换为 `data/features/followup-v2-mixed/kdconv`。轻量配置为 `configs/followup_kdconv.json`，variant为 history/fixed/gated/coverage。评测知识条件 original/missing/shuffled；shuffled来自另一对话，并不保证是错误事实。

不要直接运行 `followup_server.sh train` 就把全套视为正式实验：它会批量训练15组；先检查单种子。已同步的数据不要直接 `prepare` 重建到原目录，v2构造器要求新空目录；需要重建时使用带版本的新输出并保存diff/哈希。

## 9. 系统、论文与科研约束

`python scripts/replay_confirmed.py --output outputs/server-confirmed-replay` 仅做离线回放。新的控制器具有PROCESSING/SPEAKING/COOLDOWN/FAULT；必须周期调用tick。超时不重播，人工确认停止后恢复。同步rank调用尚无可中断超时。CrossEncoderRuntimeRanker有接口，但没有本项目正式训练检查点可供实机使用。

当前追问模型没有把点一情绪输出作为输入，不能声称两点已在学习层面闭环。同会话闭环需要新的真实对齐数据、播放/动作反馈与模块消融。服务器可继续实现服务接口，但不代替设备和用户实验。

每次实验保存原始配置、SHA256、代码版本、dirty状态、种子、输入协议、设备/显存/耗时、dev选择依据和逐样本预测。输出目录不覆盖。失败保存日志，OOM可降低batch或增加显式梯度累积，但必须记录预算变化；不能静默改标签、上下文或候选协议。

主表：点一emotion Macro-F1，辅以各类/Weighted-F1和NLL/Brier/ECE；点二MRR，辅以Recall@1/3、nDCG@1/3。三个种子分别报告，样本标准差不等于置信区间。排序配对区间使用对话簇，脚本 `scripts/compare_followup.py`；不得当成已经实现情绪F1的配对统计。FQ和KdConv、mixed和random分开报告。

继续更新论文正文、实验表与局限。结果未产生时保留待测；负结果要解释，不杜撰“显著提升”。位置编码、质量门控、注意力、温度缩放等已有技术不能仅改名成为创新。方法贡献以机制、强基线、消融和证据判定。

## 10. 服务器 Codex 第一轮实际交付

1. `docs/服务器启动检查_实际日期.md`：硬件、环境、提交、LFS/数据/模型检查及修复。
2. 可运行环境与数据状态报告，测试真实结果。
3. 小样本特征提取与首个真实数据训练的日志/检查点，或可定位的失败原因；独立任务继续推进。
4. `docs/服务器实验台账.md`：每次run路径、配置、种子、状态、最佳dev、错误和下一步。
5. 按当前代码生成实验矩阵，先单种子排错，再三种子，test在冻结后评测。
6. 据实际结果更新论文，保留审计、方法边界与未完成项。

完整可复制启动提示词同时保存在 `docs/服务器Codex启动提示词_2026-10-22.md`。请持续实际执行，不停留在复述计划；确需真人、设备或凭据时明确记录阻断，并继续可独立完成的实验。
