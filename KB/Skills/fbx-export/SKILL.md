# FBX Export Skill

将 Blender 场景中的物体导出为 FBX 文件，支持比例缩放、骨骼动画、内嵌纹理等。

## 触发条件

当用户提出以下需求时激活此 Skill：
- 导出/打包 FBX
- 从 Blender 输出模型
- 将物体转为 FBX 格式
- 批量导出 FBX

## 前置条件

- Blender 已运行且 BlenderMCP 连接正常
- 目标物体在 Blender 场景中存在

## 执行流程

### 1. 获取场景信息

```python
# 通过 BlenderMCP get_scene_info 确认物体存在
```

### 2. 执行导出

通过 BlenderMCP `execute_code` 调用导出脚本：

```python
import sys
sys.path.insert(0, '/Users/zbb/3DPipeLine/Scripts')
from export_fbx import export_fbx

# 根据用户需求设置参数
export_fbx(
    objects="目标物体名",
    scale=1.0,            # Unity 用 0.01
    armature=False,        # 角色模型开 True
    animation=False,       # 需要动画时开 True
    embed_textures=True,   # 默认内嵌纹理
    path_mode='COPY',      # 路径模式=复制
)
```

### 3. 验证输出

确认 `outPut/` 目录下生成了对应 FBX 文件且大小合理。

## 常见场景速查

| 场景 | 参数 |
|------|------|
| 静态模型（默认） | `objects="name"` |
| 导出至 Unity | `objects="name", scale=0.01` |
| 角色带骨骼动画 | `objects="name", armature=True, animation=True, scale=0.01` |
| 纯几何无纹理 | `objects="name", embed_textures=False` |
| 批量多物体 | `objects="obj1,obj2,obj3"` |
| 全场景 | 不传 objects 参数 |

## 输出位置

默认输出到 `/Users/zbb/3DPipeLine/outPut/<物体名>.fbx`

## 故障排查

1. **物体未找到**：检查物体名拼写，用 get_scene_info 列出所有物体
2. **FBX 文件为空或极小**：物体可能无网格数据，用 get_object_info 检查
3. **纹理丢失**：确认 Blender 中材质有纹理贴图，且 embed_textures=True + path_mode='COPY'
4. **导入引擎后比例错误**：调整 scale 参数（Unity=0.01）
5. **骨骼未导出**：确认 armature=True 且骨骼是物体父级
