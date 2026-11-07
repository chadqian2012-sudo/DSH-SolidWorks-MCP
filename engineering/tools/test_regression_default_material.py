# -*- coding: utf-8 -*-
"""无材几何建模与明确赋材的离线回归；不启动 SolidWorks。"""
import os
import sys
import traceback
import types
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

for _name in ("pythoncom", "win32com", "win32com.client", "win32con"):
    sys.modules[_name] = types.ModuleType(_name)
sys.modules["win32com"].client = sys.modules["win32com.client"]
sys.modules["win32com.client"].VARIANT = lambda *args: None
sys.modules["pythoncom"].VT_DISPATCH = 9

PASS = 0
FAIL = 0
FAILURES = []


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [ok] %s" % label)
    else:
        FAIL += 1
        FAILURES.append(label + ("  <" + str(detail)[:300] + ">" if detail else ""))
        print("  [FAIL] %s  %s" % (label, detail))


# ══════════════════════════════════════════════════════════════════════
def test_default_geometry(swapi):
    """未指定时不采用默认牌号、材料表或历史工况。"""
    from unittest.mock import patch
    print("\n[1] 未指定材料时只建几何")
    with patch.dict(os.environ, {"DSH_PART_MATERIAL": ""}), \
         patch.object(swapi, "default_part_material_name", side_effect=AssertionError("不得推测默认牌号")):
        result = swapi.resolve_default_material_or_raise()
        check("无参解析成功且没有材料、密度", result.get("ok") is True
              and result.get("material") is None and result.get("density") is None, result)
        check("无参默认材料为空", swapi.default_part_material() is None)
        check("只有密度也不伪造材料身份",
              swapi.resolve_default_material_or_raise(density=7850).get("density") is None)
    with patch.dict(os.environ, {"DSH_PART_MATERIAL": "6061-T6"}):
        result = swapi.resolve_default_material_or_raise()
        check("环境明确指定时保留铝材料", result.get("material") == "6061-T6", result)
        result = swapi.resolve_default_material_or_raise(material="Q235")
        check("显式参数优先于环境材料", result.get("material") == "Q235", result)


def test_missing_library_geometry(swapi):
    """缺材料库不挡几何；明确指定不存在的牌号仍失败。"""
    from unittest.mock import patch
    print("\n[2] 缺材料库不阻断未指定材料的建模")
    with patch.dict(os.environ, {"DSH_PART_MATERIAL": ""}), \
         patch.object(swapi, "is_valid_material", return_value=(False, None, "材料库缺失")):
        check("材料库缺失仍允许未指定材料",
              swapi.resolve_default_material_or_raise().get("ok") is True)
        result = swapi.resolve_default_material_or_raise(material="Q235")
        check("明确指定不可用 Q235 仍失败", result.get("ok") is False, result)
        check("错误保留明确牌号并给出登记路径",
              "Q235" in result.get("error", "")
              and "set_custom_material" in result.get("error", ""), result)


def test_density_from_grade_table(swapi):
    """契约④：密度随牌号表带出（Q235→7850），不用 SW 近似值。"""
    print("\n[3] 密度随牌号表带出（Q235 → 7850）")
    _r = swapi.resolve_default_material_or_raise(material="Q235")
    check("★ 解析器带出密度", _r.get("density") is not None, _r.get("density"))
    check("★ Q235 → 7850 kg/m³（牌号表值，不是 SW 近似 1000/7900）",
          abs(float(_r.get("density") or 0) - 7850.0) < 1e-6, _r.get("density"))
    _r2 = swapi.resolve_default_material_or_raise(material="Q235")
    check("★ 显式 material=Q235 同样带出 7850",
          abs(float(_r2.get("density") or 0) - 7850.0) < 1e-6, _r2.get("density"))
    _r3 = swapi.resolve_default_material_or_raise(material="Q235", density=7800)
    check("★ 显式 density 参数优先（不被牌号表覆盖）",
          abs(float(_r3.get("density") or 0) - 7800.0) < 1e-6, _r3.get("density"))
    _r4 = swapi.resolve_default_material_or_raise(material="6061-T6")
    check("★ 6061-T6 → 2700（铝，不是钢的 7850）",
          abs(float(_r4.get("density") or 0) - 2700.0) < 1e-6, _r4.get("density"))
    # 源：必须来自牌号表
    _info = swapi.lookup_material("Q235") or {}
    check("★ 密度取值来源是 COMMON_MATERIALS 牌号表",
          abs(float(_info.get("density") or 0) - 7850.0) < 1e-6, _info.get("density"))


def test_invalid_material_rejected(swapi):
    """契约②补充：非法材料名必须显式失败，不得放行。"""
    print("\n[4] 非法材料名 → 显式失败（不放行）")
    _r = swapi.resolve_default_material_or_raise(material="INVALID_MAT")
    check("★ 非法材料 → ok=False", _r.get("ok") is False, _r.get("ok"))
    _err = str(_r.get("error") or "")
    check("★ 错误里说明该名不是合法牌号", "不是合法牌号" in _err, _err[:200])
    check("★ 解释为何不允许（.sldprt 无可信材质身份 / 防线 material_invalid）",
          "material_invalid" in _err or "材质身份" in _err, _err[:400])
    check("★ 给出可执行路径（内置合法牌号 / save_custom_material 登记）",
          "save_custom_material" in _err or "list_materials" in _err, _err[:500])
    check("★ 不返回可直接使用的 material（ok=False 时不误导）",
          _r.get("ok") is False)
    # 取回校验保持严格：不做模糊匹配
    check("★ _material_names_match 仍是逐字比对（Q235 ≠ AISI 1020）",
          swapi._material_names_match("AISI 1020", "Q235") is False)
    check("★ _material_names_match 同名才算匹配",
          swapi._material_names_match("Q235", "Q235") is True)
    _x, _note = swapi._detect_cross_standard("Q235", "AISI 1020")
    check("★ 跨标准替代仍被判出（未被放宽）", _x is True, _note)


def test_geometry_save_and_explicit_failure(swapi):
    """两种保存入口记录未赋材事实；后续明确失败不能伪装成未指定。"""
    import json
    import tempfile
    import types
    from unittest.mock import patch
    import defense_gate
    print("\n[5] 直接新建、保存与明确赋材失败")
    with tempfile.TemporaryDirectory(prefix="dsh_geometry_material_") as td:
        def save_file(path, *args):
            Path(path).write_bytes(b"offline geometry fixture" * 256)
            return 0
        doc = types.SimpleNamespace(SketchManager=object(), FeatureManager=object(),
                                    Extension=object(), SaveAs3=save_file,
                                    GetBodies2=lambda *args: [object()])
        app = types.SimpleNamespace(ActiveDoc=None, NewDocument=lambda *args: doc)
        with patch.dict(os.environ, {"DSH_PART_MATERIAL": ""}), \
             patch.object(swapi, "_disable_snapping"), \
             patch.object(swapi, "get_part_template", return_value="offline-template"), \
             patch.object(swapi.SWModel, "_reset_plane_context"), \
             patch.object(swapi, "VISUAL_MODE", False):
            model = swapi.new_part(sw=app)
        check("new_part() 没有待赋材或伪造成功结果",
              model._pending_material is None and model.material_result is None)
        with patch.object(model, "set_material", side_effect=AssertionError("几何保存不得强制赋材")), \
             patch.object(defense_gate, "request_signed_credential", side_effect=AssertionError("无材不得签名")):
            for method in (model.save, model.save_as):
                path = os.path.join(td, method.__name__ + ".SLDPRT")
                # 同路径曾经失败，也必须由本次无材保存写入新事实。
                Path(defense_gate.attestation_path(path)).write_text(
                    json.dumps({"material_invalid": True, "requested_material": "OLD"}), encoding="utf-8")
                saved = method(path)
                att = json.loads(Path(defense_gate.attestation_path(path)).read_text(encoding="utf-8"))
                check(method.__name__ + " 成功且明确未指定、未签名",
                      saved.get("ok") is True and att.get("material_status") == "unspecified"
                      and att.get("ok") is None and not att.get("_sig")
                      and att.get("requested_material") is None, saved)
                check(method.__name__ + " 仍不能通过明确材料验证",
                      defense_gate.check_material_attestation(path).get("ok") is False)
        with patch.object(swapi, "get_sw", side_effect=AssertionError("非法请求不应接触SW")):
            try:
                swapi.new_part(material="INVALID_MAT")
            except RuntimeError:
                check("非法 new_part 在创建文档前失败", True)
            else:
                check("非法 new_part 在创建文档前失败", False)
        model.material_result = {"ok": True, "material": "Q235"}
        failed = model.set_material(None)
        check("新的明确失败覆盖旧成功，不能借用旧结果",
              failed.get("ok") is False and model.material_result is failed)
        path = os.path.join(td, "failed.SLDPRT")
        saved = model.save(path)
        att = json.loads(Path(defense_gate.attestation_path(path)).read_text(encoding="utf-8"))
        check("明确赋材失败保存后仍记失败，不变成未指定",
              att.get("material_invalid") is True and att.get("material_status") != "unspecified", att)
        check("几何任务也不能放过明确赋材失败",
              defense_gate.check_material_attestation(path, required=False).get("ok") is False)


def test_single_material_owner(swapi):
    """赋材由单一入口完成；登记材料属性不能冒充 SW 写入成功。"""
    import inspect
    print("\n[6] 材料写入只走一个入口")
    body = inspect.getsource(swapi.SWModel._try_apply_pending_material)
    check("待赋材请求只调用一次 set_material", body.count("self.set_material(") == 1)
    check("已删除重复自定义登记后再次赋材的通路",
          not hasattr(swapi.SWModel, "_apply_material_via_custom_registry"))
    check("显式自定义材料登记入口仍存在",
          callable(getattr(swapi.SWModel, "set_custom_material", None))
          and callable(getattr(swapi, "save_custom_material", None)))
    source = inspect.getsource(swapi.SWModel.set_material)
    check("已删除重复尝试同一写入的 custom-fallback", "custom-fallback" not in source)
    check("独立读回缺失或不符都必须拒绝", "if not _rb_match:" in source)
    source = inspect.getsource(swapi._pick_attested_density)
    check("凭据实测密度与期望密度保持分别记录",
          "return _d, _expected, True" in source)


# ══════════════════════════════════════════════════════════════════════
def main():
    try:
        import swapi
        check("swapi 加载的是【本工作区】这份（防双副本假失败）",
              os.path.normcase(os.path.abspath(swapi.__file__)).startswith(
                  os.path.normcase(HERE)), swapi.__file__)
        check("★ 新增 is_default_material_available",
              callable(getattr(swapi, "is_default_material_available", None)))
        check("★ 新增 resolve_default_material_or_raise",
              callable(getattr(swapi, "resolve_default_material_or_raise", None)))

        test_default_geometry(swapi)
        test_missing_library_geometry(swapi)
        test_density_from_grade_table(swapi)
        test_invalid_material_rejected(swapi)
        test_geometry_save_and_explicit_failure(swapi)
        test_single_material_owner(swapi)
    except Exception:
        global FAIL
        FAIL += 1
        FAILURES.append("未捕获异常（见下方 traceback）")
        traceback.print_exc()

    print("\n" + "=" * 70)
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    if FAILURES:
        print("失败项：")
        for _f in FAILURES:
            print("  - " + _f)
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
