import cv2

from deepface import DeepFace


def count_faces(image_path):
    """
    使用DeepFace检测图像中的人脸数量并标记

    参数:
        image_path (str): 图像文件的路径

    返回:
        int: 检测到的人脸数量
    """
    try:
        # 使用DeepFace进行人脸检测
        face_objs = DeepFace.extract_faces(
            img_path=image_path, detector_backend="opencv", enforce_detection=False
        )

        # 读取原始图像
        img = cv2.imread(image_path)

        # 人脸计数器
        face_count = 0

        # 遍历每个检测到的人脸
        for i, face_obj in enumerate(face_objs):
            # 提取人脸区域坐标
            facial_area = face_obj["facial_area"]
            x, y, w, h = (
                facial_area["x"],
                facial_area["y"],
                facial_area["w"],
                facial_area["h"],
            )

            # 在图像上绘制矩形框标记人脸
            cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)

            # 添加人脸编号
            cv2.putText(
                img,
                f"Face {i + 1}",
                (x, y - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (0, 255, 0),
                2,
            )

            face_count += 1

        # 显示结果图像
        cv2.imshow("Detected Faces", img)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

        # 保存标记后的图像（可选）
        output_path = "output_" + image_path
        cv2.imwrite(output_path, img)
        print(f"标记后的图像已保存为: {output_path}")

        return face_count

    except Exception as e:
        print(f"检测过程中出现错误: {str(e)}")
        return 0


# 使用示例
if __name__ == "__main__":
    image_path = "1.jpg"  # 替换为你的图片路径

    num_faces = count_faces(image_path)
    print(f"在图像中检测到 {num_faces} 张人脸")
