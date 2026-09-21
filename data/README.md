# 数据目录

**MELD（创新点一，2026-09-15）：**`raw/meld` 保存官方三份标注、音视频归档与来源校验；`processed/meld` 保存对话索引、标签审计和去重协议；`features/meld-v1` 留给服务器提取的正式缓存。方案与命令见 [创新点一_数据与训练.md](../创新点一_数据与训练.md)。不把 MELD 情绪标签转换为 HOLD/SHIFT。

- examples：手写中文示例，允许随代码保存；只验证软件。
- raw：官方原始数据，本地保存，不修改。
- processed：标准化和审计后的数据。
- features：冻结编码器特征缓存，尚未生成真实特征。

后三个目录被 Git 忽略，不会自动上传或迁移。

本次下载 FQ-Bank 的 dev.json，来源为 https://raw.githubusercontent.com/amazon-science/fq-bank/main/data/dev.json ，下载日期 2026-09-14。下载后查询到官方 HEAD 为 d164b804d29022ec1e4b79faf4603d5758c8162b，原始文件 SHA256 为 4445457da1cf7a2c2d35bffc84ecf2caf471a7a6ce0016e0e96122c69abb28dd，共 7,299,666 字节。以内容哈希为本次复现依据；远程 main 后续可能变化。

该数据的许可为 CC BY-SA 4.0，详见 [官方许可证](https://github.com/amazon-science/fq-bank/blob/main/LICENSE)。本分支只保存转换代码和数据来源说明，未把下载数据加入版本管理。

FQ-Bank 转换器当前会过滤候选正负标签冲突记录并去重，审计结果在 processed 文件旁的 .meta.json。此清理版协议不是原论文原样复现，正式比较前需冻结协议并对所有模型统一应用。

其余论文相关数据尚未下载，不存在已准备好完整多模态训练集的假设。
