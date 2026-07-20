"""地图 (仙工 .smap) 表 —— 描述已上传的 2D 导航地图。

- 一次只有一张 is_active=True 的活跃地图,前端主渲染这张
- 具体的站点/线段几何数据不落库,启动/上传时解析文件到内存缓存 (map_service._geometry_cache)
- 只落表:文件路径 + header 元数据(坐标范围/分辨率/版本),用于列表展示 + 快速切换
"""

from tortoise import fields
from tortoise.models import Model


class Map(Model):
    id = fields.IntField(pk=True)
    uuid = fields.CharField(max_length=64, unique=True, description="业务 uuid")
    name = fields.CharField(max_length=128, description="地图显示名, e.g. 车间A-一层")
    filename = fields.CharField(max_length=256, description="磁盘文件名(data/maps/ 下的相对路径)")

    # 从 .smap header 拆出便于列表页展示,不重复读文件
    map_name = fields.CharField(max_length=128, null=True, description=".smap header.mapName")
    map_type = fields.CharField(max_length=32, default="2D-Map", description=".smap header.mapType")
    version = fields.CharField(max_length=16, null=True, description=".smap header.version")
    resolution = fields.FloatField(default=0.02, description="栅格分辨率 米/格")

    min_x = fields.FloatField(default=0.0)
    min_y = fields.FloatField(default=0.0)
    max_x = fields.FloatField(default=0.0)
    max_y = fields.FloatField(default=0.0)

    point_count = fields.IntField(default=0, description="advancedPointList 数量,列表页展示用")
    curve_count = fields.IntField(default=0, description="advancedCurveList 数量")

    is_active = fields.BooleanField(default=False, description="当前活跃地图(全表最多一张)")

    uploaded_by = fields.CharField(max_length=64, null=True, description="上传者用户名")
    uploaded_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "map"
        ordering = ["-uploaded_at"]

    def __str__(self) -> str:
        return f"<Map {self.name} ({self.filename})>"
