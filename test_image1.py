from deepface import DeepFace

# 分析图片中的人脸属性
try:
    # 使用DeepFace的analyze函数分析面部属性
    analysis_results = DeepFace.analyze(
        img_path="2.png", actions=["age", "gender", "emotion", "race"]
    )

    # 处理单张人脸情况（结果直接是字典）
    if isinstance(analysis_results, dict):
        result = analysis_results
    else:  # 多张人脸情况（结果是列表）
        result = analysis_results[0]  # 取第一张人脸

    # 打印分析结果
    print("人脸特征分析结果：")
    print(f"估计年龄: {result['age']} 岁")
    print(f"主要性别: {result['dominant_gender']}")
    print(f"主要情绪: {result['dominant_emotion']}")
    print(f"主要种族: {result['dominant_race']}")

    # 打印详细情绪分析
    print("\n详细情绪分析:")
    for emotion, score in result["emotion"].items():
        print(f"  {emotion}: {score:.2f}%")

except Exception as e:
    print(f"分析过程中出现错误: {e}")
