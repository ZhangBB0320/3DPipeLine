# Blender FBX 导出规则

## 导出前检查

1. **确认物体存在**：导出前必须检查物体名在场景中存在，不存在则报错而非静默跳过
2. **确认材质完整**：如果使用内嵌纹理，检查材质是否有纹理贴图链接
3. **应用变换**：默认 `apply_transforms=True`，确保旋转/缩放已烘焙到顶点数据，避免导入引擎后位置偏移

## 命名规范

- 单物体导出：`outPut/<物体名>.fbx`
- 多物体合并导出：`outPut/<场景名或自定义名>.fbx`
- 文件名使用英文/拼音，避免中文路径导致引擎解析异常

## 比例规范

- 默认 `scale=1.0`（Blender 原始尺寸）
- 导出到 Unity 时必须 `scale=0.01`
- 导出前如果物体在 Blender 中尺寸异常（如 AI 生成模型经常极大或极小），应先在 Blender 中归一化尺寸后再导出

## 骨骼与动画

- `armature=True` 时，object_types 必须包含 ARMATURE
- `animation=True` 必须与 `armature=True` 配合使用，单独开动画无意义
- 带骨骼导出时，确保骨骼是物体的父级且 Armature Modifier 正确绑定

## 纹理处理

- 默认 `embed_textures=True` + `path_mode='COPY'`，纹理打包进 FBX
- 如果纹理文件极大（>50MB），考虑 `embed_textures=False` + `path_mode='RELATIVE'`
- AI 生成模型通常只有颜色贴图，无 PBR 多通道贴图

## 导出后验证

- 检查文件大小是否合理（空文件/过大文件都可能是异常）
- 导出后在目标引擎中做基本验证：位置/缩放/材质显示
