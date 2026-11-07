"""原生放样曲面扩展；保留原有 SWModel 实现。"""
import math
import os
import win32com.client

from swapi import MM, SWModel, _com_get, _feature_type_name, _is_sketch_feature


def saved_duct_geometry_matches(sw, path, geometry_sha256):
    """核对已保存的单配置风管曲面及文件摘要；查询失败交由材料检查处理。"""
    from gate_integrity import _sha256
    doc = sw.GetOpenDocumentByName(path)
    model = win32com.client.CastTo(doc, "IModelDoc2")
    part = win32com.client.CastTo(doc, "IPartDoc")
    solids, sheets = part.GetBodies2(0, False), part.GetBodies2(1, False)
    loft = win32com.client.CastTo(part.FeatureByName("DSH_Duct_Surface"), "IFeature")
    return (
        os.path.normcase(os.path.realpath(model.GetPathName())) ==
        os.path.normcase(os.path.realpath(path))
        and not model.GetSaveFlag() and model.GetType() == 1
        and model.GetConfigurationCount() == 1
        and (solids is None or isinstance(solids, (tuple, list)) and len(solids) == 0)
        and isinstance(sheets, (tuple, list)) and len(sheets) == 1
        and loft is not None and loft.GetTypeName2() == "BlendRefSurface"
        and isinstance(geometry_sha256, str) and bool(geometry_sha256)
        and _sha256(path) == geometry_sha256)


class SurfaceModel(SWModel):
    def ellipse(self, cx, cy, width_mm, height_mm):
        """在当前草图创建水平长轴椭圆；宽、高均为全轴长度（mm）。"""
        cx, cy, w, h = map(float, (cx, cy, width_mm, height_mm))
        if not all(map(math.isfinite, (cx, cy, w, h))) or not w > h > 0:
            raise ValueError("椭圆需要有限坐标，并满足 宽 > 高 > 0（毫米）")
        self._ensure_sketch_active()
        self._ensure_area_context()
        x, y = cx * MM, (cy + self._surface_sketch_offset) * MM
        segment = self.skm.CreateEllipse(x, y, 0, x + w * MM / 2, y, 0,
                                         x, y + h * MM / 2, 0)
        if segment is None:
            raise RuntimeError("SolidWorks 未创建椭圆")
        self._sketch_area_mm2 += math.pi * w * h / 4
        return segment

    def loft_surface(self, sketch_names):
        """按顺序连接已退出编辑的截面草图，返回新建的原生放样曲面特征。"""
        if not isinstance(sketch_names, (tuple, list)) or len(sketch_names) < 2:
            raise ValueError("放样至少需要两个有序草图名")
        if any(not isinstance(n, str) or not n.strip() for n in sketch_names):
            raise ValueError("截面必须使用特征树中的草图名")
        if len({n.casefold() for n in sketch_names}) != len(sketch_names):
            raise ValueError("同一草图不能重复用作截面")
        if self.skm.ActiveSketch is not None:
            raise RuntimeError("请先退出截面草图，再创建放样")

        def surface_lofts():
            return {f.Name: f for f in self.fm.GetFeatures(True) or ()
                    if _feature_type_name(f) == "BlendRefSurface"}

        previous = surface_lofts().keys()
        part = win32com.client.CastTo(self.model, "IPartDoc")
        self.model.ClearSelection2(True)
        try:
            for i, name in enumerate(sketch_names):
                feature = part.FeatureByName(name)
                if not _is_sketch_feature(feature) or not feature.Select2(i > 0, 1):
                    raise RuntimeError("无法选中放样截面草图：" + name)
            if self.model.SelectionManager.GetSelectedObjectCount2(1) != len(sketch_names):
                raise RuntimeError("SolidWorks 选中的截面数量不符")
            # Port of Auto-SolidWorks NativeFeatures.cs at 1bf68f4 (LICENSE.Auto-SolidWorks).
            self.model.InsertLoftRefSurface2(False, False, False, 1.0, 0, 0)
            if not self.model.ForceRebuild3(False):
                raise RuntimeError("放样后模型无法重建；请检查截面")
            lofts = surface_lofts()
            created = lofts.keys() - previous
            if len(created) != 1:
                raise RuntimeError("未找到唯一的新放样曲面特征；请检查截面顺序和形状")
            loft = lofts[created.pop()]
            faces = _com_get(loft, "GetFaces")
            if not faces or any(face.GetBody().GetType() != 1 for face in faces):
                raise RuntimeError("放样特征没有生成可用的曲面体")
            self._consumed_sketches.update(sketch_names)
            if self._active_sketch_name in sketch_names:
                self._mark_sketch_consumed()
            return loft
        finally:
            self.model.ClearSelection2(True)
