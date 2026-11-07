# -*- coding: utf-8 -*-
r"""回归测试：COM 晚绑定属性/方法混淆（R11）+ 材质写入不可靠（R9）

★ 锁定的真实事故（台账 BUG-71/74/77/85/92 + BUG-30/55/56）：
  COM 侧：
    1. 把【属性】当【方法】调用、或反之 —— IComponent2.GetSurface/GetEdges、
       IFace2.Normal、IModelDoc2.RevisionNumber 都踩过；
    2. com_error「参数个数不正确」= 早期/晚绑定签名混用
       （IModelDoc2.GetMassProperties 无参，却传了 (0)）；
    3. 现有代码到处 try/except 猜「这是属性还是方法」，猜错被吞成静默 None。
  材质侧：
    4. SW 材质库没有国标牌号（Q235/45/40Cr/20CrMnTi/GCr15/HT200/ZL104…）；
    5. 跨标准替代被误判（把国标近似成欧美标，Q235 → AISI 1020）；
    6. **实测 33/35 件 sw_internal_matches_request=false** —— 凭据与实物脱节。

★ 本测试钉死的契约（全部假 COM，不启动 SolidWorks）：
  A. 类型库判定表与官方一致：方法必须加括号、属性绝不能加括号；
  B. _com_get 对方法一律调用、对属性一律不加括号（不再靠 callable() 猜）；
  C. 成员缺失 / 签名不符 → 抛 ComMemberError（可读），不是静默 None；
  D. GetMassProperties 必须按【无参方法】调用（传 (0) 会被判为错误）；
  E. 材质写入后必须【读回校验】，不一致必须报错；
  F. 跨标准替代必须被【拒绝】（不允许静默近似成欧美标）；
  G. 缺国标牌号时必须给出可操作路径（set_custom_material / 自定义 .sldmat）。

依据（写死在断言里，防止有人「顺手改回去」）：
  【类型库】%LOCALAPPDATA%\Temp\gen_py\3.13\
            83A33D31-27C5-11CE-BFD4-00400513BB57x0x33x0.py
    判定规则：方法 → 生成 def Foo(self, ...)；
              属性 → 只出现在 _prop_map_get_ / _prop_map_put_ 字典里。
    · IFace2.Normal                   L21163  _prop_map_get_ → **属性**
    · IFace2.GetBox / GetSurface      L20604 / L20604 区段 → **方法**
    · ISurface.IsPlane / IsCylinder / Identity  L62439 / L62423 / L62394 → **方法**
      （本文件旧注释写「静态绑定下是属性，加括号会 TypeError」—— 那是错的）
    · IModelDoc2.GetMassProperties    L38295  def GetMassProperties(self) → 方法，**无参**
    · IModelDocExtension.GetMassProperties  L41895 → 方法，(Accuracy, Status) 两参
      （把两者混用就是 com_error「参数个数不正确」的直接来源）
    · ISldWorks.RevisionNumber        L60473  → **方法**
    · ISldWorks.GetMaterialDatabases  L59525  → **方法**
    · IPartDoc.GetMaterialPropertyName2  L47356 → 方法，(ConfigName, Database)
    · IPartDoc.SetMaterialPropertyName2  L47898 → 方法，(ConfigName, Database, Name)
    · IPartDoc.MaterialIdName         L47915  _prop_map_get_ → **属性**
    · IComponent2.Name2 / Transform2  L10416 / L10426 → **属性**
  【网页】SOLIDWORKS API Help「Get Material Example (VBA)」
    https://help.solidworks.com/2026/English/api/sldworksapi/Get_Material_Example_VB.htm
    「sMatName = swPart.GetMaterialPropertyName2("Default", sMatDB)」
    —— 证实它是**方法**且带 out 参数 Database。
  【网页】SOLIDWORKS API Help「Get Mass Properties Example (VBA)」
    https://help.solidworks.com/2026/English/api/sldworksapi/Get_Mass_Properties_of_ActiveDoc_Example_VB.htm
    「vMassProp = swModelExt.GetMassProperties2(1, nStatus, True)」
    —— 带参的是 ModelDocExtension 版；ModelDoc2 版无参。
  【网页】pywin32 Issue #1896（方法/属性在 late-binding 下的表现差异）
    https://github.com/mhammond/pywin32/issues/1896

⚠️ 全部用假 COM 对象 / monkeypatch：不 import 真 COM，不启动 SolidWorks。

运行：python test_regression_com_material.py
退出码：0 = 全部通过；1 = 有失败
"""
import ast
import io
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── stub pywin32（必须在 import swapi 之前）──────────────────────────────
for _n in ("pythoncom", "win32com", "win32com.client", "win32con"):
    sys.modules.setdefault(_n, types.ModuleType(_n))
sys.modules["win32com"].client = sys.modules["win32com.client"]
sys.modules["win32com.client"].VARIANT = lambda *a, **k: None
sys.modules["win32com.client"].dynamic = types.SimpleNamespace(
    Dispatch=lambda *a, **k: None)
sys.modules["win32com.client"].gencache = types.SimpleNamespace(
    EnsureDispatch=lambda *a, **k: None)
sys.modules["pythoncom"].VT_DISPATCH = 9
sys.modules["pythoncom"].VT_ARRAY = 8192
sys.modules["pythoncom"].VARIANT = lambda *a, **k: None

import swapi  # noqa: E402

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
        FAILURES.append(label + ("  <" + str(detail)[:400] + ">" if detail else ""))
        print("  [FAIL] %s  %s" % (label, detail))


def sec(t):
    print("")
    print("=" * 74)
    print(t)
    print("=" * 74)


SRC = io.open(os.path.join(HERE, "swapi.py"), encoding="utf-8").read()
TREE = ast.parse(SRC)


def _func(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _src_of(name):
    fn = _func(name)
    return (ast.get_source_segment(SRC, fn) or "") if fn is not None else ""


# ══════════════════════════════════════════════════════════════════════
# 假 COM：可切换「成员是属性还是方法」「方法签名」「回读材质名」
# ══════════════════════════════════════════════════════════════════════
class FakeMassPropsModel(object):
    """假 IModelDoc2：GetMassProperties 是【无参方法】（类型库 L38295）。"""

    def __init__(self, arr=None, strict_arity=True):
        self._arr = arr or [0, 0, 0, 0.0001, 0.01, 0.000785, 0, 0, 0, 0, 0, 0]
        self._strict = strict_arity
        self.calls = []

    def GetMassProperties(self, *args):
        self.calls.append(args)
        if self._strict and args:
            raise TypeError("GetMassProperties 不接受实参（收到 %d 个）" % len(args))
        return tuple(self._arr)


class FakeRevisionSw(object):
    """假 ISldWorks：RevisionNumber 是【方法】（类型库 L60473）。"""

    def RevisionNumber(self):
        return "33.5.0"


class FakeBody(object):
    def __init__(self, doc):
        self._doc = doc

    def GetMassProperties(self, accuracy=0):
        return [0, 0, 0, self._doc._vol, 0.01, self._doc._mass, 0, 0, 0, 0, 0, 0]

    def GetBodyBox(self):
        return [-0.05, -0.04, -0.005, 0.05, 0.04, 0.005]

    def SetMaterialProperty(self, cfg, db, name):
        return 1        # 非 0 = 失败（实测恒 1/6）


class FakeMaterialDoc(object):
    """假 IPartDoc + IModelDoc2，用于 set_material 全链路。

    mode:
      "ok"          —— 写入成功且回读一致
      "silent_swap" —— SW 静默接受调用，但内部材质被换成另一个（33/35 事故现场）
      "no_write"    —— 完全没写进去，回读仍是旧材料
    """

    def __init__(self, mode="ok", req="Q235", actual="Q235",
                 volume_m3=0.0001, mass_kg=0.785):
        # mass/volume = 0.785/0.0001 = 7850 kg/m³（钢），与 Q235 期望密度一致，
        # 否则会先被"密度/家族不符"拦掉，测不到读回校验那条路径。
        self.mode = mode
        self._req = req
        self._actual = actual
        self._vol = volume_m3
        self._mass = mass_kg
        self._bodies = 1
        self.calls = []
        self.write_calls = []

    # ---- IModelDoc2 ----
    def GetMassProperties(self, *args):
        if args:
            raise TypeError("GetMassProperties 不接受实参")
        return [0, 0, 0, self._vol, 0.01, self._mass, 0, 0, 0, 0, 0, 0]

    def GetBodies2(self, body_type=0, visible_only=False):
        return (FakeBody(self),) if self._bodies else None

    def ForceRebuild3(self, top_only=False):
        return True

    @property
    def ConfigurationManager(self):
        return types.SimpleNamespace(
            ActiveConfiguration=types.SimpleNamespace(Name="默认"))

    # ---- IPartDoc：方法 ----
    def SetMaterialPropertyName2(self, cfg, db, name):
        self.write_calls.append((cfg, db, name))
        if self.mode == "silent_swap":
            self._actual = "AISI 1020"
        elif self.mode == "no_write":
            self._actual = "普通碳钢"
        else:
            self._actual = name
        return None      # void

    def GetMaterialPropertyName2(self, cfg="", db=None):
        return self._actual

    # ---- IPartDoc：属性（绝不能加括号）----
    @property
    def MaterialIdName(self):
        return self._actual


def make_swmodel(doc=None):
    doc = doc or FakeMaterialDoc()
    m = object.__new__(swapi.SWModel)
    m.sw = None
    m.model = doc
    m.skm = types.SimpleNamespace(ActiveSketch=None, InsertSketch=lambda f=True: True)
    m.fm = types.SimpleNamespace()
    m.ext = types.SimpleNamespace()
    m._empty = None
    m.warnings = []
    m._pending_material = None
    m._pending_density = None
    m.material_result = None
    m._requested_material = None
    m._material_source = None
    m._active_sketch_name = None
    m._consumed_sketches = set()
    m._last_cut_feature_name = None
    return m


# ══════════════════════════════════════════════════════════════════════
sec("A. 类型库判定表：方法 / 属性必须分清")
# ══════════════════════════════════════════════════════════════════════
_METHODS = set(getattr(swapi, "_COM_METHOD_NAMES", frozenset()))
check("★ 新增 _COM_METHOD_NAMES 判定表", len(_METHODS) > 0, len(_METHODS))
check("★ 新增 ComMemberError 异常类型",
      isinstance(getattr(swapi, "ComMemberError", None), type))
check("★ 新增 _com_get / _com_get_soft",
      callable(getattr(swapi, "_com_get", None))
      and callable(getattr(swapi, "_com_get_soft", None)))

for _n in ("GetMassProperties", "GetBox", "GetSurface", "GetEdges",
           "RevisionNumber", "GetMaterialDatabases",
           "GetMaterialPropertyName2", "SetMaterialPropertyName2",
           "IsPlane", "IsCylinder", "Identity",
           "FirstFeature", "GetNextFeature", "GetTypeName2",
           "GetTitle", "GetPathName"):
    check("方法 %r 在判定表里（类型库是 def）" % _n, _n in _METHODS, _n)

for _n in ("Normal", "MaterialIdName", "MaterialUserName",
           "MaterialPropertyValues", "Name2", "Transform2"):
    check("属性 %r 【不】在判定表里（类型库是 _prop_map_）" % _n,
          _n not in _METHODS, _n)

# ══════════════════════════════════════════════════════════════════════
sec("B. _com_get：方法一定调用、属性一定不加括号（不靠 callable 猜）")
# ══════════════════════════════════════════════════════════════════════


class _Both(object):
    """同时提供「可调用」与「不可调用」两种成员，验证判定不靠 callable。"""

    def __init__(self):
        self.method_called = 0

    def GetMassProperties(self, *a):
        self.method_called += 1
        return ("called", a)

    @property
    def Normal(self):
        return [0.0, 0.0, 1.0]


_o = _Both()
_r = swapi._com_get(_o, "GetMassProperties")
check("★ 方法被调用（返回值来自方法体）", _r == ("called", ()), _r)
check("★ 方法确实被调用了一次", _o.method_called == 1, _o.method_called)

_r2 = swapi._com_get(_o, "Normal")
check("★ 属性原样返回（不是调用结果）", _r2 == [0.0, 0.0, 1.0], _r2)


class _CallableProp(object):
    """陷阱：属性值本身是可调用的（函数/类/代理）—— callable() 会误判。"""

    def __init__(self):
        self.calls = 0

    @property
    def Normal(self):
        def _inner():
            self.calls += 1
            return "WRONG"
        return _inner


_cp = _CallableProp()
_r3 = swapi._com_get(_cp, "Normal")
check("★ 可调用属性【不】被调用（callable() 猜法会在这里出错）",
      _cp.calls == 0 and callable(_r3), (_cp.calls, type(_r3)))


class _ImplicitlyEvaluated(object):
    def GetMassProperties(self):
        return "IMPLICIT"


_ie = _ImplicitlyEvaluated()
_ie.__dict__["GetMassProperties"] = (1, 2, 3, 4, 5, 6)
_r4 = swapi._com_get(_ie, "GetMassProperties")
check("★ 无参方法已被隐式求值时原样返回（不强行调用）",
      _r4 == (1, 2, 3, 4, 5, 6), _r4)

try:
    swapi._com_get(_ie, "Normal", 1, 2)
    _argp = False
except Exception:
    _argp = True
check("★ 属性传实参 → 抛错（不静默吞）", _argp)

# ══════════════════════════════════════════════════════════════════════
sec("C. 成员缺失 / 签名不符 → ComMemberError（可读，不是静默 None）")
# ══════════════════════════════════════════════════════════════════════
try:
    swapi._com_get(_o, "__no_such_member__")
    _miss = False
    _miss_msg = ""
except swapi.ComMemberError as _e:
    _miss = True
    _miss_msg = str(_e)
except Exception as _e:
    _miss = False
    _miss_msg = "wrong type: %r" % (_e,)
check("★ 成员不存在 → ComMemberError", _miss, _miss_msg[:200])
check("★ 错误信息包含成员名（可读）", "__no_such_member__" in _miss_msg,
      _miss_msg[:200])

try:
    swapi._com_get(None, "GetMassProperties")
    _none = False
    _none_msg = ""
except swapi.ComMemberError as _e:
    _none = True
    _none_msg = str(_e)
except Exception as _e:
    _none = False
    _none_msg = "wrong type"
check("★ obj=None → ComMemberError", _none, _none_msg[:200])


class _StrictArity(object):
    def GetMassProperties(self):
        raise TypeError("takes 1 positional argument but 2 were given")


try:
    swapi._com_get(_StrictArity(), "GetMassProperties", 0)
    _arity = False
    _arity_msg = ""
except swapi.ComMemberError as _e:
    _arity = True
    _arity_msg = str(_e)
except Exception as _e:
    _arity = False
    _arity_msg = "wrong type: %r" % (_e,)
check("★ 方法实参不匹配 → ComMemberError（把 com_error 翻译成人话）",
      _arity, _arity_msg[:200])
check("★ 错误信息提示核对类型库形参个数",
      "形参" in _arity_msg or "实参" in _arity_msg, _arity_msg[:200])

check("_com_get_soft 缺成员返回默认值",
      swapi._com_get_soft(_o, "__nope__", default="D") == "D")
check("_com_get_soft 成功时返回真值",
      swapi._com_get_soft(_o, "Normal") == [0.0, 0.0, 1.0])

# ══════════════════════════════════════════════════════════════════════
sec("D. GetMassProperties 必须按【无参方法】调用")
# ══════════════════════════════════════════════════════════════════════
_md = FakeMassPropsModel()
_m = make_swmodel()
_m.model = _md
_res = _m.massprops()
check("massprops() 在假 COM 上成功", _res.get("ok") is True, _res.get("error"))
check("★ GetMassProperties 被【无参】调用（旧代码先按属性取，恒失败）",
      _md.calls and all(len(a) == 0 for a in _md.calls), _md.calls)
check("★ 体积来源就是 GetMassProperties（不再静默降级）",
      _res.get("volume_source") == "GetMassProperties", _res.get("volume_source"))

_md2 = FakeMassPropsModel()
_m2 = make_swmodel()
_m2.model = _md2
_vol = _m2._body_volume_mm3()
check("★ _body_volume_mm3 用无参方法读（旧代码传 (0) 抛 com_error）",
      _md2.calls and all(len(a) == 0 for a in _md2.calls), _md2.calls)
check("_body_volume_mm3 拿到正确体积（0.0001 m³ = 100000 mm³）",
      _vol is not None and abs(_vol - 100000.0) < 1.0, _vol)

check("★ 源码里不再有 self.model.GetMassProperties(0)（签名混用）",
      "self.model.GetMassProperties(0)" not in SRC,
      [ln for ln in SRC.split(chr(10))
       if "self.model.GetMassProperties(0)" in ln][:3])

# ══════════════════════════════════════════════════════════════════════
sec("E. RevisionNumber：方法，必须加括号")
# ══════════════════════════════════════════════════════════════════════
_maj = swapi._early_major_from_sw(FakeRevisionSw())
check("★ RevisionNumber 按方法调用能取到 33", _maj == 33, _maj)
check("★ 源码里不再用 callable() 猜 RevisionNumber",
      "if callable(rev):" not in _src_of("_early_major_from_sw"),
      _src_of("_early_major_from_sw")[:300])


class _PropRevision(object):
    @property
    def RevisionNumber(self):
        return "33.5.0"


check("属性式 RevisionNumber 也能读到（兼容）",
      swapi._early_major_from_sw(_PropRevision()) == 33,
      swapi._early_major_from_sw(_PropRevision()))
check("None 一律返回 0（不抛）", swapi._early_major_from_sw(None) == 0)

# ══════════════════════════════════════════════════════════════════════
sec("F. 材质名归一化：只做无损归一，绝不做模糊匹配")
# ══════════════════════════════════════════════════════════════════════
check("★ 新增 _normalize_material_name / _material_names_match",
      callable(getattr(swapi, "_normalize_material_name", None))
      and callable(getattr(swapi, "_material_names_match", None)))

_same = [
    ("SolidWorks Materials|普通碳钢", "普通碳钢"),
    ("solidworks materials|AISI 1020", "AISI 1020"),
    ("  AISI 1020  ", "AISI 1020"),
    ("AISI  1020", "AISI 1020"),
    ("SolidWorks Materials - 灰铸铁", "灰铸铁"),
]
for _a, _b in _same:
    check("归一化后判同一材料: %r == %r" % (_a, _b),
          swapi._material_names_match(_a, _b) is True)

_cross = [
    ("AISI 1020", "Q235"),
    ("普通碳钢", "Q235"),
    ("S235JR", "Q235"),
    ("SUS304", "06Cr19Ni10"),
    ("AISI 1045", "45"),
]
for _a, _b in _cross:
    check("★ 跨标准替代判【不匹配】: %r vs %r" % (_a, _b),
          swapi._material_names_match(_a, _b) is False)

check("空名一律不匹配",
      swapi._material_names_match("", "Q235") is False
      and swapi._material_names_match("Q235", "") is False)

# ══════════════════════════════════════════════════════════════════════
sec("G. 读回校验：写入后 SW 内部材质 != 请求 → 必须报错")
# ══════════════════════════════════════════════════════════════════════
check("★ 新增 _read_back_material",
      callable(getattr(swapi, "_read_back_material", None)))

_doc_ok = FakeMaterialDoc(mode="ok")
_rb = swapi._read_back_material(make_swmodel(_doc_ok))
check("★ 回读优先用 GetMaterialPropertyName2（方法）",
      _rb.get("method") == "GetMaterialPropertyName2", _rb)
check("回读拿到正确材料名", _rb.get("name") == "Q235", _rb)


class _PropOnlyDoc(FakeMaterialDoc):
    def GetMaterialPropertyName2(self, cfg="", db=None):
        raise AttributeError("GetMaterialPropertyName2 不可用（假 COM）")


_rb2 = swapi._read_back_material(make_swmodel(_PropOnlyDoc(mode="ok")))
check("★ 方法不可用时回落到属性 MaterialIdName",
      _rb2.get("method") == "MaterialIdName" and _rb2.get("name") == "Q235",
      _rb2)

_doc_ok = FakeMaterialDoc(mode="ok", req="Q235", actual="Q235")
_m = make_swmodel(_doc_ok)
_res = _m.set_material("Q235")
check("★ 写入成功且回读一致 → ok=True", _res.get("ok") is True,
      (_res.get("error"), _res.get("readback_material")))
check("★ 返回体固化读回证据 sw_internal_matches_request=True",
      _res.get("sw_internal_matches_request") is True,
      _res.get("sw_internal_matches_request"))
check("返回体带 readback_material",
      _res.get("readback_material") == "Q235", _res.get("readback_material"))

_doc_swap = FakeMaterialDoc(mode="silent_swap", req="Q235", actual="Q235")
_m = make_swmodel(_doc_swap)
_res = _m.set_material("Q235")
check("★ SW 静默换成 AISI 1020 → 必须 ok=False（33/35 事故现场）",
      _res.get("ok") is False, (_res.get("ok"), _res.get("readback_material")))
check("★ 报错里点明「读回不一致」并给出实际材质",
      ("读回不一致" in str(_res.get("error")))
      and ("AISI 1020" in str(_res.get("error"))),
      str(_res.get("error"))[:400])
check("★ 记录 readback_mismatch 审计明细",
      bool(_res.get("readback_mismatch")), _res.get("readback_mismatch"))

_doc_now = FakeMaterialDoc(mode="no_write", req="Q235", actual="Q235")
_m = make_swmodel(_doc_now)
_res = _m.set_material("Q235")
check("★ 完全没写进去（回读仍是旧材料）→ ok=False",
      _res.get("ok") is False, (_res.get("ok"), _res.get("readback_material")))

# ══════════════════════════════════════════════════════════════════════
sec("H. 跨标准替代必须被拒（BUG-55）")
# ══════════════════════════════════════════════════════════════════════
check("★ set_material 新增 allow_cross_standard 参数",
      "allow_cross_standard" in (_src_of("set_material") or ""))
check("★ 新增 _gb_material_advice（缺国标牌号的可操作路径）",
      callable(getattr(swapi, "_gb_material_advice", None)))

_adv = swapi._gb_material_advice("Q235")
check("★ 建议明确停止自动重试且不把Python登记当作SW赋材",
      "不要自动重建或重复赋材" in _adv and "不会创建 SW 材质" in _adv, _adv)
check("★ 建议里给出 set_custom_material 这条可执行路径",
      "set_custom_material" in _adv, _adv[:400])
check("★ 建议里给出自定义 .sldmat / Custom Materials 这条路径",
      ("Custom Materials" in _adv) or ("sldmat" in _adv), _adv[:400])
check("★ 建议里明确禁止「近似成欧美标」",
      ("严禁" in _adv) and ("欧美标" in _adv), _adv[:400])
check("★ 建议里给出显式放行的正规开关 allow_cross_standard=True",
      "allow_cross_standard=True" in _adv, _adv[:400])

_x_std, _x_note = swapi._detect_cross_standard("Q235", "AISI 1020")
check("★ Q235 → AISI 1020 判定为跨标准", _x_std is True, _x_note)
check("跨标准说明里带屈服强度差异佐证",
      "屈服" in _x_note or "MPa" in _x_note, _x_note[:200])
_x_std2, _ = swapi._detect_cross_standard("Q235", "Q235")
check("同名不算跨标准", _x_std2 is False)

_cut = _src_of("set_material")
_i_x = _cut.find("if _cross_std and not allow_cross_standard:")
check("★ 源码里跨标准分支存在", _i_x >= 0)
if _i_x >= 0:
    _seg = _cut[_i_x:_i_x + 900]
    check("★ 跨标准分支里有 continue（拒绝该候选，不是只加警告）",
          "continue" in _seg, _seg[:400])
    check("★ 跨标准被拒时记录 cross_standard_rejected 审计明细",
          "cross_standard_rejected" in _seg, _seg[:400])

# ══════════════════════════════════════════════════════════════════════
sec("I. 材质写入失败路径：三类原因都要可读")
# ══════════════════════════════════════════════════════════════════════
_m = make_swmodel(FakeMaterialDoc(mode="silent_swap"))
_res = _m.set_material("Q235")
check("★ 失败时 ok=False", _res.get("ok") is False)
check("★ 失败时给出 hint（可操作路径）",
      bool(_res.get("hint"))
      and ("set_custom_material" in str(_res.get("hint"))),
      str(_res.get("hint"))[:400])
check("★ 失败时 material_mismatch_rejected=True（供材料防线拦截）",
      _res.get("material_mismatch_rejected") is True,
      _res.get("material_mismatch_rejected"))
check("★ 失败时同时标记 rejected_readback_mismatch",
      _res.get("rejected_readback_mismatch") is True,
      _res.get("rejected_readback_mismatch"))

# ══════════════════════════════════════════════════════════════════════
sec("J. 别名、输出参数与失败停点")
from unittest.mock import patch
import tempfile
import json
from pathlib import Path
import defense_gate

import subprocess
_cold_check = subprocess.run([sys.executable, "-S", "-c",
    "import sys\nsys.path.insert(0, sys.argv[1])\n"
    "from physics.material_db import material_names_match\n"
    "assert material_names_match('Q235（碳素结构钢）', 'Q235')\n"
    "assert not material_names_match('AISI 1020', 'Q235')\n"
    "assert 'pythoncom' not in sys.modules\n", HERE], capture_output=True, text=True)
check("纯Python冷启动识别外部国标中文别名且不拉入COM",
      _cold_check.returncode == 0, _cold_check.stderr)

class _TupleReadbackDoc(FakeMaterialDoc):
    def GetMaterialPropertyName2(self, cfg, database):
        self.calls.append((cfg, database))
        return self._actual, "SolidWorks Materials"

_tuple_doc = _TupleReadbackDoc(actual="普通碳钢")
_tuple_doc.ConfigurationManager.ActiveConfiguration.Name = "默认"
_tuple_model = make_swmodel(_tuple_doc)
_read = swapi._read_back_material(_tuple_model)
check("generated dispatch 的(name,database)分别读取",
      _read.get("name") == "普通碳钢" and _read.get("database") == "SolidWorks Materials", _read)
check("getter 使用活动配置名", _tuple_doc.calls[-1][0] == "默认", _tuple_doc.calls)
check("get_material 与独立读回共用同一名称读取",
      _tuple_model.get_material().get("name") == "普通碳钢")

class _ByrefReadbackDoc(FakeMaterialDoc):
    def GetMaterialPropertyName2(self, cfg, database):
        database.value = "Custom Materials"
        return "Q235"

with patch.object(swapi.win32com.client, "VARIANT", side_effect=lambda *args: types.SimpleNamespace(value="")):
    _read = swapi._read_back_material(make_swmodel(_ByrefReadbackDoc()))
check("动态COM的BSTR输出参数保留材质库名",
      _read.get("name") == "Q235" and _read.get("database") == "Custom Materials", _read)
check("已登记的中英文命名别名按同一canonical识别",
      swapi._material_names_match("SolidWorks Materials|普通碳钢", "PlainCarbonSteel") is True)
check("命名别名仍不接受跨标准替代",
      not swapi._material_names_match("AISI 1020", "Q235")
      and not swapi._material_names_match("S235JR", "Q235"))

class _ChineseAliasDoc(FakeMaterialDoc):
    def SetMaterialPropertyName2(self, cfg, database, name):
        self.write_calls.append((cfg, database, name))
        self._actual = "普通碳钢"

with patch.object(swapi, "_resolve_material_names", return_value=[("普通碳钢", "exact")]), \
     patch.object(swapi.time, "sleep"):
    _alias_model = make_swmodel(_ChineseAliasDoc())
    _alias_result = _alias_model.set_material("PlainCarbonSteel")
check("明确PlainCarbonSteel、读回普通碳钢可以成功",
      _alias_result.get("ok") is True and _alias_result.get("sw_internal_matches_request") is True,
      _alias_result)

with tempfile.TemporaryDirectory(prefix="sw_alias_attestation_") as _td, \
     patch.object(defense_gate, "request_signed_credential",
                  side_effect=lambda spec: {"ok": True, "credential": dict(spec, _sig="offline-fixture")}):
    _alias_path = os.path.join(_td, "alias.SLDPRT")
    Path(_alias_path).write_bytes(b"offline alias geometry" * 256)
    _warning_count = len(_alias_model.warnings)
    _alias_model._emit_material_attestation(_alias_path, {"ok": True})
    check("已确认的别名在签发凭据时不再误报替换警告",
          len(_alias_model.warnings) == _warning_count, _alias_model.warnings)

check("同一库和分类下不同牌号不能因公共目录判同材",
      not swapi._material_names_match("SW|Steel|Q355", "SW|Steel|Q235")
      and not swapi._material_names_match("foo|bar|one", "foo|bar|two"))

_part = FakeMaterialDoc()
_strict_model = types.SimpleNamespace(_oleobj_=object(),
    ConfigurationManager=_part.ConfigurationManager,
    GetMassProperties=_part.GetMassProperties, ForceRebuild3=_part.ForceRebuild3)
_part_constructor = lambda raw: _part
with patch.object(swapi, "get_early_module", return_value=types.SimpleNamespace(IPartDoc=_part_constructor)) as _interface, \
     patch.object(swapi, "_resolve_material_names", side_effect=lambda db, name, **kw: [(name, "exact")]), \
     patch.object(swapi.time, "sleep"):
    _strict = make_swmodel(_strict_model)
    check("只有IModelDoc2成员的对象经IPartDoc读取实体", _strict._has_solid_body())
    _strict_result = _strict.set_material("Q235")
    check("早绑定模型通过IPartDoc赋材与读回",
          _strict_result.get("ok") is True and bool(_interface.call_args_list),
          _strict_result)

_late_part = FakeMaterialDoc()
_late_part._oleobj_ = object()
with patch.object(swapi, "get_early_module", return_value=None):
    check("类型库不可用时保留可用晚绑定接口",
          make_swmodel(_late_part)._part_doc is _late_part)

class _EmptyReadbackDoc(FakeMaterialDoc):
    def SetMaterialPropertyName2(self, cfg, database, name):
        self.write_calls.append((cfg, database, name))
    def GetMaterialPropertyName2(self, cfg, database):
        return ""
    @property
    def MaterialIdName(self):
        return "旧的材料属性"

_empty_doc = _EmptyReadbackDoc()
_empty_model = make_swmodel(_empty_doc)
_empty_model._pending_material = "Q235"
_empty_model._pending_density = 7850
with tempfile.TemporaryDirectory(prefix="sw_material_stop_") as _td, \
     patch.object(swapi, "_resolve_material_names", side_effect=lambda db, name, **kw: [(name, "exact")]), \
     patch.object(swapi.time, "sleep"), \
     patch.object(defense_gate, "request_signed_credential", return_value={"ok": False, "error": "offline"}), \
     patch.object(defense_gate, "note_unsigned_attempt"):
    _failed = _empty_model._try_apply_pending_material()
    _write_count = len(_empty_doc.write_calls)
    check("空独立读回不能借旧密度或旧属性判成功",
          _failed.get("ok") is False and _failed.get("readback_material") is None, _failed)
    check("明确赋材失败要求人工处理，禁止自动重试",
          _failed.get("requires_user_action") is True and _failed.get("retryable") is False)
    _empty_model._try_apply_pending_material()
    _save_count = [0]
    def _save(path, *args):
        _save_count[0] += 1
        Path(path).write_bytes(("fixture-%d" % _save_count[0]).encode() * 600)
        return 0
    _empty_doc.SaveAs3 = _save
    _empty_doc.GetPathName = lambda: ""
    for _i in range(2):
        _empty_model.save(os.path.join(_td, "failed.SLDPRT"))
    check("失败后的实体钩子及重复save均不再调用COM赋材",
          len(_empty_doc.write_calls) == _write_count, _empty_doc.write_calls)

    for _actual, _matches in (("Q235", True), ("AISI 1020", False)):
        _source = os.path.join(_td, "source.SLDPRT")
        Path(_source).write_bytes(b"previously saved fixture" * 256)
        Path(defense_gate.attestation_path(_source)).write_text(
            json.dumps({"requested_material": "Q235"}), encoding="utf-8")
        _doc = FakeMaterialDoc(actual=_actual)
        _doc.GetPathName = lambda: _source
        _doc.SketchManager = _doc.FeatureManager = _doc.Extension = object()
        _doc.SaveAs3 = _save
        _reopened = swapi.from_active(sw=types.SimpleNamespace(ActiveDoc=_doc))
        _target = os.path.join(_td, "reopened-%s.SLDPRT" % _matches)
        _reopened.save_as(_target)
        _att = json.loads(Path(defense_gate.attestation_path(_target)).read_text(encoding="utf-8"))
        check("重开后另存保留请求并独立核对实际材料：" + _actual,
              _att.get("requested_material") == "Q235"
              and _att.get("sw_internal_matches_request") is _matches
              and _att.get("material_status") != "unspecified" and not _doc.write_calls,
              _att)

# ══════════════════════════════════════════════════════════════════════
print("")
print("=" * 74)
print("汇总：PASS=%d  FAIL=%d" % (PASS, FAIL))
if FAILURES:
    print("失败项：")
    for x in FAILURES:
        print("  - " + x)
print("")
print("真机验证（需 SW 在跑，人工执行）：")
print("  · 对同一零件连续 set_material 两次，第二次读回必须是第二次的牌号")
print("  · set_material('Q235') 在中文库环境应失败并给出 _gb_material_advice")
print("  · massprops() 必须走 GetMassProperties 路径（volume_source 可辨）")
sys.exit(0 if FAIL == 0 else 1)
