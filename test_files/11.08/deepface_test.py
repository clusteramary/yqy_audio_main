try:
    from deepface import DeepFace
    print("victory")
except ImportError as e:
    print("false")

# result=DeepFace.verify(img1_path="a.jpg",img2_path="b.jpg",enforce_detection=False)
# print(result)

result=DeepFace.analyze(img_path="a.jpg",actions=['age','gender','emotion','race'])
print(result)
result=DeepFace.analyze(img_path="b.jpg",actions=['age','gender','emotion','race'])
print(result)
result=DeepFace.analyze(img_path="a.jpg",actions=['age','gender','emotion','race'])
print(result)
result=DeepFace.analyze(img_path="b.jpg",actions=['age','gender','emotion','race'])
print(result)
result=DeepFace.analyze(img_path="a.jpg",actions=['age','gender','emotion','race'])
print(result)
result=DeepFace.analyze(img_path="b.jpg",actions=['age','gender','emotion','race'])
print(result)