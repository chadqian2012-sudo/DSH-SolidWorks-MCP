# -*- coding: utf-8 -*-
"""Create or resize a circle-to-ellipse surface duct via sw_bridge run.

DSH_DUCT_SPEC points to an absolute JSON file. Dimensions are millimeters.
create: {"mode":"create", "inlet_diameter_mm":40, "outlet_width_mm":60,
         "outlet_height_mm":30, "length_mm":80, "output_path":"...SLDPRT"}
resize: {"mode":"resize", "outlet_width_mm":70, "output_path":"...SLDPRT"}

Uses the native loft extension in sw_surface.
"""
import json
import math
import os
import tempfile
import pythoncom
import win32com.client
import swapi
import sw_bridge
import sw_surface
from gate_integrity import _sha256

INLET = "DSH_Duct_Inlet"
OUTLET = "DSH_Duct_Outlet"
PLANE = "DSH_Duct_OutletPlane"
LOFT = "DSH_Duct_Surface"


def prop(obj, name):
    value = getattr(obj, name)
    return value() if callable(value) and not hasattr(value, "_oleobj_") else value


def same_path(doc, path):
    actual = os.path.normcase(os.path.realpath(str(prop(doc, "GetPathName"))))
    expected = os.path.normcase(os.path.realpath(path))
    if actual != expected:
        raise RuntimeError("Document path mismatch; refusing to modify or close")


def positive(spec, name):
    value = spec[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(name + " must be a positive finite number")
    return float(value)


def body_count(part, kind):
    bodies = part.GetBodies2(kind, False)
    if bodies is None:
        return 0
    if isinstance(bodies, (tuple, list)):
        return len(bodies)
    raise RuntimeError("Unrecognized body query result")


def xyz(point):
    return tuple(float(prop(point, axis)) for axis in ("X", "Y", "Z"))


def dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def ellipse_data(segment):
    segment = win32com.client.CastTo(segment, "ISketchEllipse")
    center = xyz(prop(segment, "GetCenterPoint2"))
    major = xyz(prop(segment, "GetMajorPoint2"))
    minor = xyz(prop(segment, "GetMinorPoint2"))
    return center, major, minor, 2000 * dist(center, major), 2000 * dist(center, minor)


def assert_ellipse(segment, width, height):
    data = ellipse_data(segment)
    if abs(data[3] - width) > 0.01 or abs(data[4] - height) > 0.01:
        raise RuntimeError("Outlet ellipse axes mismatch: %.6f x %.6f mm" % data[3:])
    return data


def named_feature(doc, name):
    feature = win32com.client.CastTo(doc, "IPartDoc").FeatureByName(name)
    if feature is None:
        raise RuntimeError("Required feature missing: " + name)
    return feature


def single_segment(doc, name):
    sketch = prop(named_feature(doc, name), "GetSpecificFeature2")
    segments = prop(sketch, "GetSketchSegments")
    if not isinstance(segments, (tuple, list)) or len(segments) != 1:
        raise RuntimeError("Expected exactly one segment in " + name)
    return segments[0]


def sketch(doc, name):
    return prop(named_feature(doc, name), "GetSpecificFeature2")


def sketch_model_point(sw, sk, coords):
    utility = prop(sw, "GetMathUtility")
    values = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, tuple(coords))
    point = utility.CreatePoint(values)
    transform = prop(prop(sk, "ModelToSketchTransform"), "Inverse")
    mapped = point.MultiplyTransform(transform)
    return tuple(float(v) for v in prop(mapped, "ArrayData"))


def inspect(sw, doc):
    if prop(doc, "GetConfigurationCount") != 1:
        raise RuntimeError("Duct requires exactly one configuration; refusing to edit or save")
    part = win32com.client.CastTo(doc, "IPartDoc")
    counts = body_count(part, 0), body_count(part, 1)
    if counts != (0, 1):
        raise RuntimeError("Expected zero solid and one surface body, got %r" % (counts,))
    warning = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BOOL, False)
    feature = win32com.client.dynamic.DumbDispatch(named_feature(doc, LOFT))
    feature._FlagAsMethod("GetErrorCode2")
    code = feature.GetErrorCode2(warning)
    if int(code) != 0:
        raise RuntimeError("Surface feature reports code %r (warning=%r)" % (code, warning.value))
    named_feature(doc, PLANE)
    arc = win32com.client.CastTo(single_segment(doc, INLET), "ISketchArc")
    radius = float(prop(arc, "GetRadius"))
    inlet = 2000 * radius
    center = xyz(prop(arc, "GetCenterPoint2"))
    outlet = ellipse_data(single_segment(doc, OUTLET))
    if dist(center, (0.0, 0.0, 0.0)) > 1e-5 or dist(outlet[0], (0.0, 0.0, 0.0)) > 1e-5:
        raise RuntimeError("Section centers are not at their sketch origins")
    inlet_sk, outlet_sk = sketch(doc, INLET), sketch(doc, OUTLET)
    inlet_origin = sketch_model_point(sw, inlet_sk, (0.0, 0.0, 0.0))
    outlet_origin = sketch_model_point(sw, outlet_sk, (0.0, 0.0, 0.0))
    inlet_z = sketch_model_point(sw, inlet_sk, (0.0, 0.0, 1.0))
    outlet_z = sketch_model_point(sw, outlet_sk, (0.0, 0.0, 1.0))
    axis = tuple(z - o for z, o in zip(inlet_z, inlet_origin))
    other_axis = tuple(z - o for z, o in zip(outlet_z, outlet_origin))
    separation = tuple(b - a for a, b in zip(inlet_origin, outlet_origin))
    axis_norm = dist(axis, (0.0, 0.0, 0.0))
    other_norm = dist(other_axis, (0.0, 0.0, 0.0))
    if axis_norm < 1e-12 or other_norm < 1e-12:
        raise RuntimeError("Invalid sketch normal")
    normal = tuple(v / axis_norm for v in axis)
    parallel = abs(sum(a * b for a, b in zip(normal, other_axis))) / other_norm
    projection = sum(a * b for a, b in zip(normal, separation))
    lateral = tuple(d - projection * n for d, n in zip(separation, normal))
    length = 1000 * abs(projection)
    if length <= 0 or parallel < 0.999999 or dist(lateral, (0.0, 0.0, 0.0)) > 1e-5:
        raise RuntimeError("Section planes are not parallel and coaxial")
    # Each sampled profile point must lie on a distinct open boundary edge.
    bodies = part.GetBodies2(1, False)
    edges = prop(bodies[0], "GetEdges")
    if not isinstance(edges, (tuple, list)):
        raise RuntimeError("Unable to enumerate surface edges")
    boundary = []
    for edge in edges:
        adjacent = prop(edge, "GetTwoAdjacentFaces2")
        if not isinstance(adjacent, (tuple, list)) or len(adjacent) != 2:
            raise RuntimeError("Unable to determine edge adjacency")
        if sum(face is not None for face in adjacent) == 1:
            boundary.append(edge)
    inlet_points = [tuple(center[i] + (sign * radius if i == 0 else 0) for i in range(3)) for sign in (-1, 1)]
    outlet_points = [outlet[1], tuple(2 * c - p for c, p in zip(outlet[0], outlet[1]))]
    matches = []
    for sk, points in ((inlet_sk, inlet_points), (outlet_sk, outlet_points)):
        side = set()
        for point in points:
            model_point = sketch_model_point(sw, sk, point)
            found = set()
            for index, edge in enumerate(boundary):
                closest = edge.GetClosestPointOn(*model_point)
                if isinstance(closest, (tuple, list)) and len(closest) >= 3 and dist(model_point, closest[:3]) < 1e-5:
                    found.add(index)
            if not found:
                raise RuntimeError("Section point has no open boundary edge")
            side.update(found)
        matches.append(side)
    if matches[0] & matches[1]:
        raise RuntimeError("Inlet and outlet boundaries are not distinct")
    return inlet, outlet[3], outlet[4], length


def rebuild(doc):
    if not doc.ForceRebuild3(False):
        raise RuntimeError("SolidWorks rebuild failed")


def save(doc, path):
    # Silent save-as adopts the new pathname; option 2 would keep editing the source.
    error = doc.SaveAs3(path, 0, 1)
    if error != 0:
        raise RuntimeError("SolidWorks save failed: %r" % (error,))
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise RuntimeError("Save did not create a nonempty file")
    same_path(doc, path)
    if bool(prop(doc, "GetSaveFlag")):
        raise RuntimeError("Document is still modified after save")


def rename_feature(sw, doc, original, new, kinds):
    feature = prop(doc, "FirstFeature")
    while feature is not None:
        kind = prop(feature, "GetTypeName2")
        if kind in kinds:
            candidate = prop(feature, "GetSpecificFeature2")
            if candidate is not None and int(sw.IsSame(original, candidate)) == 1:
                feature.Name = new
                named_feature(doc, new)
                return feature
        feature = prop(feature, "GetNextFeature")
    raise RuntimeError("Cannot match created object to its feature: " + new)


def create(sw, spec, path):
    inlet = positive(spec, "inlet_diameter_mm")
    width = positive(spec, "outlet_width_mm")
    height = positive(spec, "outlet_height_mm")
    length = positive(spec, "length_mm")
    if width <= height:
        raise ValueError("outlet_width_mm must be greater than outlet_height_mm")
    if sw.GetOpenDocumentByName(path) is not None:
        raise RuntimeError("Target document is already open")
    if os.path.exists(path):
        raise FileExistsError("Output already exists")
    if not os.path.isdir(os.path.dirname(path)):
        raise FileNotFoundError("Output directory does not exist")
    template = swapi.get_part_template(sw)
    if not template:
        raise RuntimeError("No part template")
    doc = sw.NewDocument(template, 0, 0.1, 0.1)
    if doc is None:
        raise RuntimeError("Failed to create part")
    m = sw_surface.SurfaceModel(sw, doc)
    m.begin_sketch("Front Plane")
    active_inlet = m.skm.ActiveSketch
    m.circle(0, 0, inlet / 2)
    m.end_sketch(merge=False)
    rename_feature(sw, doc, active_inlet, INLET, ("ProfileFeature", "Sketch"))
    plane = m._make_offset_plane("Front Plane", length)
    if not plane.get("ok") or plane.get("feature") is None:
        raise RuntimeError("Offset plane failed: %r" % (plane,))
    # InsertRefPlane returns IRefPlane; naming and selection belong to IFeature.
    plane_feature = rename_feature(sw, doc, plane["feature"], PLANE, ("RefPlane",))
    m.begin_sketch_on_feature(plane_feature, label=PLANE)
    active_outlet = m.skm.ActiveSketch
    segment = m.ellipse(0, 0, width, height)
    assert_ellipse(segment, width, height)
    m.end_sketch(merge=False)
    rename_feature(sw, doc, active_outlet, OUTLET, ("ProfileFeature", "Sketch"))
    loft = m.loft_surface([INLET, OUTLET])
    if loft is None:
        raise RuntimeError("Surface loft failed")
    loft.Name = LOFT
    rebuild(doc)
    actual = inspect(sw, doc)
    if any(abs(a - b) > 0.01 for a, b in zip(actual, (inlet, width, height, length))):
        raise RuntimeError("Created section dimensions differ: %r" % (actual,))
    # Unique title avoids colliding with an unrelated open document's title.
    temp_dir = tempfile.mkdtemp(prefix=".dsh_duct_", dir=os.path.dirname(path))
    temp_path = os.path.join(temp_dir, os.path.basename(temp_dir) + ".SLDPRT")
    save(doc, temp_path)
    same_path(doc, temp_path)
    if bool(prop(doc, "GetSaveFlag")):
        raise RuntimeError("New document is dirty; refusing to close")
    sw.CloseDoc(str(prop(doc, "GetTitle")))
    if sw.GetOpenDocumentByName(temp_path) is not None:
        raise RuntimeError("Created document did not close; output remains at " + temp_path)
    # Windows rename fails if the destination was created by another process.
    os.rename(temp_path, path)
    os.rmdir(temp_dir)
    doc, info = sw_bridge._open_doc6(sw, path, 1, options=1)
    if doc is None or not info.get("ok"):
        raise RuntimeError("Published file saved but reopening failed: %r" % (info,))
    same_path(doc, path)
    reopened = inspect(sw, doc)
    if any(abs(a - b) > 0.01 for a, b in zip(reopened, actual)):
        raise RuntimeError("Reopened dimensions differ: %r" % (reopened,))
    return {"ok": True, "mode": "create", "path": path, "sections_and_length_mm": reopened}


def resize(sw, spec, path):
    width = positive(spec, "outlet_width_mm")
    if not os.path.isfile(path):
        raise ValueError("Existing SLDPRT required for resize")
    doc = sw.GetOpenDocumentByName(path)
    if doc is not None:
        active = sw.ActiveDoc
        if active is None:
            raise RuntimeError("Target is open but not the active document")
        same_path(active, path)
        doc = active
    else:
        doc, info = sw_bridge._open_doc6(sw, path, 1, options=1)
        if doc is None or not info.get("ok"):
            raise RuntimeError("Cannot open part: %r" % (info,))
    same_path(doc, path)
    if bool(prop(doc, "GetSaveFlag")):
        raise RuntimeError("Opened document is already modified")
    inlet, old_width, height, length = inspect(sw, doc)
    if width <= height:
        raise ValueError("New width must exceed current outlet height")
    doc.ClearSelection2(True)
    if not named_feature(doc, OUTLET).Select2(False, 0):
        raise RuntimeError("Could not select named outlet sketch")
    doc.EditSketch()
    attempted = False
    try:
        active = doc.SketchManager.ActiveSketch
        if active is None or int(sw.IsSame(active, sketch(doc, OUTLET))) != 1:
            raise RuntimeError("Unexpected active sketch; refusing to edit")
        segment = win32com.client.CastTo(single_segment(doc, OUTLET), "ISketchEllipse")
        center, major, minor, _, _ = assert_ellipse(segment, old_width, height)
        target = tuple(c + (p - c) * width / old_width for c, p in zip(center, major))
        attempted = True
        if not prop(segment, "GetMajorPoint2").SetCoords(*target):
            raise RuntimeError("SolidWorks rejected in-place axis edit")
        assert_ellipse(segment, width, height)
        doc.SketchManager.InsertSketch(True)
        if doc.SketchManager.ActiveSketch is not None:
            raise RuntimeError("Could not exit the sketch")
        rebuild(doc)
        actual = inspect(sw, doc)
        if any(abs(a - b) > 0.01 for a, b in zip(actual, (inlet, width, height, length))):
            raise RuntimeError("Rebuilt dimensions differ: %r" % (actual,))
    except Exception as error:
        try:
            if doc.SketchManager.ActiveSketch is None:
                doc.ClearSelection2(True)
                if not named_feature(doc, OUTLET).Select2(False, 0):
                    raise RuntimeError("Cannot select sketch for recovery")
                doc.EditSketch()
            active = doc.SketchManager.ActiveSketch
            if active is None or int(sw.IsSame(active, sketch(doc, OUTLET))) != 1:
                raise RuntimeError("Cannot safely identify sketch for recovery")
            if attempted:
                original = win32com.client.CastTo(single_segment(doc, OUTLET), "ISketchEllipse")
                if not prop(original, "GetMajorPoint2").SetCoords(*major):
                    raise RuntimeError("SolidWorks refused to restore the original axis")
                assert_ellipse(original, old_width, height)
            doc.SketchManager.InsertSketch(True)
            if doc.SketchManager.ActiveSketch is not None:
                raise RuntimeError("Cannot exit recovered sketch")
            rebuild(doc)
            inspect(sw, doc)
        except Exception as recovery:
            raise RuntimeError("Edit failed (%r); automatic recovery failed (%r). No save attempted; inspect the open document." % (error, recovery)) from error
        raise RuntimeError("Edit failed and geometry was restored; no save attempted: %r" % (error,)) from error
    same_path(doc, path)
    save(doc, path)
    return {"ok": True, "mode": "resize", "path": path,
            "old_width_mm": old_width, "sections_mm": actual}


def main(sw):
    spec_path = os.environ.get("DSH_DUCT_SPEC", "")
    if not os.path.isabs(spec_path):
        raise ValueError("DSH_DUCT_SPEC must be an absolute JSON file path")
    with open(spec_path, encoding="utf-8-sig") as stream:
        spec = json.load(stream)
    if not isinstance(spec, dict):
        raise ValueError("Specification must be a JSON object")
    mode = spec.get("mode")
    required = {"mode", "output_path", "outlet_width_mm"}
    if mode == "create":
        required.update(("inlet_diameter_mm", "outlet_height_mm", "length_mm"))
    elif mode != "resize":
        raise ValueError("mode must be create or resize")
    if set(spec) != required:
        raise ValueError("Missing fields: %s; unsupported fields: %s" %
                         (sorted(required - spec.keys()), sorted(spec.keys() - required)))
    path = spec.get("output_path", "")
    if not isinstance(path, str) or not os.path.isabs(path) or not path.lower().endswith(".sldprt"):
        raise ValueError("output_path must be an absolute .SLDPRT path")
    path = os.path.abspath(path)
    if mode == "create":
        return create(sw, spec, path)
    return resize(sw, spec, path)


__RESULT__ = main(sw)
__RESULT__["artifacts"] = [__RESULT__["path"]]
__RESULT__["geometry_sha256"] = _sha256(__RESULT__["path"])
if not sw_surface.saved_duct_geometry_matches(sw, __RESULT__["path"], __RESULT__["geometry_sha256"]):
    raise RuntimeError("Saved part does not match the expected single lofted sheet")
