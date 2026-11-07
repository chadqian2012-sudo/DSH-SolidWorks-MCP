"""Prepare drawing pages and embedded PDF text for visual interpretation.

Text is evidence, not confirmed dimensions. No measurements are inferred.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def json_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return [json_value(item) for item in value]
    except TypeError:
        return str(value)


def converted_dwg_tags(stream, warnings):
    from ezdxf.lldxf.tagger import ascii_tags_loader
    from ezdxf.lldxf.tags import group_tags

    section = None
    for tags in group_tags(ascii_tags_loader(stream)):
        if tags[0].value == "SECTION":
            section = tags.get_first_value(2, None)
        if (section == "OBJECTS" and tags[0].value == "DICTIONARY"
                and tags.get_first_value(330, None) == "0"):
            removed = 0
            index = len(tags) - 1
            while index > 0:
                if (tags[index - 1].code == 3 and tags[index - 1].value == ""
                        and tags[index].code in (350, 360) and tags[index].value == "0"):
                    del tags[index - 1:index + 1]
                    removed += 1
                    index -= 1
                index -= 1
            if removed:
                warnings.append("DWG 转换结果的根管理字典含 %d 个空名字、空指针条目，读取时已忽略；"
                                "其余标签及原始转换文件保持不变。仍需对照原图核验转换完整性。" % removed)
        yield from tags


def read_dxf(source, result, output_dir, preview_limit):
    try:
        import ezdxf
        from ezdxf.lldxf.encoding import decode_dxf_unicode
    except ImportError as exc:
        raise RuntimeError("读取 DXF 需要安装 ezdxf；现有工程的 ac_validate.py 也使用该库") from exc
    try:
        from ezdxf.lldxf.validator import is_binary_dxf_file

        if "converted_dxf" in result and not is_binary_dxf_file(source):
            from ezdxf.document import Drawing
            from ezdxf.filemanagement import dxf_file_info

            with source.open(encoding=dxf_file_info(source).encoding, errors="surrogateescape") as stream:
                doc = Drawing.load(converted_dwg_tags(stream, result["warnings"]))
            doc.filename = str(source)
        else:
            doc = ezdxf.readfile(str(source))
    except (ezdxf.DXFError, ValueError) as exc:
        raise RuntimeError("无法解析 DXF，请从原 CAD 导出标准 DXF 或 PDF: " + str(exc)) from exc
    layouts = list(doc.layouts)
    blocks = [block for block in doc.blocks if not block.block_record.is_any_layout]
    entities = []
    spaces = [(layout, "layout") for layout in layouts] + [(block, "block_definition") for block in blocks]
    for space, space_kind in spaces:
        for entity in space:
            kind = entity.dxftype()
            item = {"type": kind, "handle": entity.dxf.get("handle"),
                    "layer": entity.dxf.get("layer"),
                    "space": space.name, "source_space": space_kind,
                    "attributes": {k: json_value(v) for k, v in entity.dxf.all_existing_dxf_attribs().items()}}
            if kind == "LWPOLYLINE":
                item["points_xyseb"] = [list(point) for point in entity.get_points("xyseb")]
            if kind == "POLYLINE":
                item["vertices"] = [{k: json_value(v) for k, v in p.dxf.all_existing_dxf_attribs().items()} for p in entity.vertices]
            if kind == "SPLINE":
                item["control_points"] = [list(p) for p in entity.control_points]
                item["fit_points"] = [list(p) for p in entity.fit_points]
                item["knots"] = [float(k) for k in entity.knots]
                item["weights"] = [float(w) for w in entity.weights]
            if kind == "MTEXT":
                item["raw_text"] = entity.text
            if kind == "DIMENSION":
                geometry = entity.get_geometry_block()
                item["rendered_text"] = []
                for part in geometry or []:
                    if part.dxftype() not in ("TEXT", "MTEXT"):
                        continue
                    layer = doc.layers.get(part.dxf.layer) if part.dxf.layer in doc.layers else None
                    if part.dxf.get("invisible") or layer and (layer.is_off() or layer.is_frozen()):
                        continue
                    item["rendered_text"].append(part.plain_text() if part.dxftype() == "MTEXT" else part.dxf.text)
                if entity.dxf.get("dimstyle", "STANDARD") in doc.dimstyles:
                    item["dimension_style_overrides"] = {
                        k: json_value(v) for k, v in entity.override().dimstyle_attribs.items()}
                else:
                    result["warnings"].append("标注 %s 的样式 %s 缺失，无法核对显示规则。" %
                                              (entity.dxf.handle, entity.dxf.dimstyle))
                if geometry is None:
                    result["warnings"].append("标注 %s（%s）没有显示图形，预览可能缺少该尺寸；不会根据定义点补画。" %
                                              (entity.dxf.handle, space.name))
            if kind == "INSERT":
                item["attached_attributes"] = [{k: json_value(v) for k, v in attribute.dxf.all_existing_dxf_attribs().items()}
                                               for attribute in entity.attribs]
                if doc.blocks.get(entity.dxf.name) is None:
                    result["warnings"].append("块引用 %s 的块 %s 缺失，图形无法完整显示。" %
                                              (entity.dxf.handle, entity.dxf.name))
            entities.append(item)
    for block in blocks:
        if block.block.dxf.flags & 12:
            result["warnings"].append("外部引用 %s 未加载：%s；预览不包含该文件的图形。" %
                                      (block.name, block.block.dxf.get("xref_path", "")))
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = output_dir / "dxf-entities.json"
    styles = [{k: json_value(v) for k, v in style.dxf.all_existing_dxf_attribs().items()} for style in doc.dimstyles]
    with evidence_path.open("x", encoding="utf-8") as evidence:
        json.dump({"entities": entities, "dimension_styles": styles,
                   "source_warnings": result["warnings"]}, evidence, ensure_ascii=False)
    if len(result["warnings"]) > 8:
        result["warnings"] = result["warnings"][:7] + [
            "其余 %d 条源数据缺失告警见 entity_evidence 的 source_warnings；不能视为完整图纸。" % (len(result["warnings"]) - 7)]
    previews = []
    preview_chars = 0
    for entity in entities:
        if entity["source_space"] != "layout" or entity["type"] not in ("DIMENSION", "TEXT", "MTEXT"):
            continue
        layer = doc.layers.get(entity["layer"]) if entity["layer"] in doc.layers else None
        if entity["attributes"].get("invisible") or layer and (layer.is_off() or layer.is_frozen()):
            continue
        if entity["type"] == "DIMENSION" and not entity.get("rendered_text"):
            continue
        text = " / ".join(entity.get("rendered_text", [])) or str(entity.get("raw_text", entity["attributes"].get("text", "")))
        text = re.sub(r"(?<!\\)\\U\+[0-9A-Fa-f]{4}(?![0-9A-Fa-f])",
                      lambda match: (decode_dxf_unicode(match.group())
                                     if not 0xD800 <= int(match.group()[3:], 16) <= 0xDFFF else match.group()), text)
        item = {"type": entity["type"], "handle": entity["handle"], "layer": entity["layer"],
                "space": entity["space"], "source_space": entity["source_space"], "text": text[:300]}
        if entity["type"] == "DIMENSION":
            item["text_override"] = entity["attributes"].get("text", "")
        size = len(json.dumps(item, ensure_ascii=False))
        if len(previews) >= 20 or preview_chars + size > 6000:
            break
        previews.append(item)
        preview_chars += size
    result.update(ok=any(len(layout) for layout in layouts), pages=0, entity_count=len(entities),
                  entity_counts=dict(Counter(e["type"] for e in entities)),
                  entity_evidence=str(evidence_path),
                  annotation_preview=previews,
                  drawing_units_code=doc.header.get("$INSUNITS", 0),
                  layout_count=len(layouts), source_space="layouts_and_block_definitions")
    if not result["ok"]:
        result["warnings"].append("所有模型空间和图纸布局均无图元，没有可读取的图纸内容。")
    result["warnings"].extend([
        "DIMENSION 原始属性及文字只是候选，尺寸值、标注样式、文字覆盖和对应几何必须核对；文件单位代码不保证标注单位。",
        "原始数据包含布局和块定义；文字摘要排除块定义、隐藏图元及关闭或冻结图层。块坐标未经插入变换，不能当作实际位置；显示尺寸文字取自原有标注图形，不重新计算。摘要最多 20 条且截断长文字。",
        "预览受字体、外部引用、代理对象和布局视口支持影响，可能有缺失，不可仅凭预览认定图纸完整；缺失、冲突和不清晰数据应集中询问用户。"])
    if preview_limit:
        try:
            from ezdxf.addons.drawing.matplotlib import qsave
            from PIL import Image
        except ImportError:
            result["warnings"].append("缺少 matplotlib，DXF 预览不可用；请安装 matplotlib 或使用 CAD 打开。")
        else:
            nonempty_layouts = [(index, layout) for index, layout in enumerate(layouts, 1) if len(layout)]
            for index, layout in nonempty_layouts[:preview_limit]:
                image_path = output_dir / ("dxf-preview-%04d.png" % index)
                if image_path.exists():
                    raise FileExistsError("预览图片已存在，请选择空目录")
                try:
                    qsave(layout, image_path, bg="#FFFFFF", dpi=120, size_inches=(12, 9))
                    if not image_path.is_file() or not image_path.stat().st_size:
                        raise RuntimeError("预览图片为空")
                    with Image.open(image_path) as image:
                        if all(low == high == 255 for low, high in image.convert("RGB").getextrema()):
                            raise RuntimeError("预览仅有白色背景，没有可见图形，不能视为图纸已显示")
                    result["images"].append({"page": index, "path": str(image_path), "layout": layout.name})
                except Exception as exc:
                    result["warnings"].append("布局 %s 预览失败: %s" % (layout.name, exc))
            result["pages"] = len(result["images"])
            if len(nonempty_layouts) > preview_limit:
                result["warnings"].append("仅尝试预览前 %d 个非空空间，共有 %d 个；可调整 --preview-limit。" % (preview_limit, len(nonempty_layouts)))
    return result


def run(command):
    return subprocess.run(command, capture_output=True, timeout=120, check=True)


def prepare(source, output_dir, dwg2dxf=None, preview_limit=3, page=None):
    source = Path(source).resolve(strict=True)
    result = {"ok": False, "input": str(source), "pages": 0,
              "extracted_text": [], "images": [], "ocr": "not_run",
              "warnings": ["图中文字仅供候选，必须结合页图确认数值、尺寸对象和单位；缺失或模糊时向用户询问。禁止从像素比例推测尺寸。"]}
    suffix = source.suffix.lower()
    if page is not None and suffix != ".pdf":
        raise ValueError("--page 仅用于 PDF")
    if suffix == ".dxf":
        return read_dxf(source, result, output_dir, preview_limit)
    if suffix == ".dwg":
        local_converter = Path(sys.executable).with_name("dwg2dxf.exe")
        converter = dwg2dxf or shutil.which("dwg2dxf") or (local_converter if local_converter.is_file() else None)
        if not converter:
            raise RuntimeError("未找到 LibreDWG 的 dwg2dxf；请提供 --dwg2dxf 可执行文件路径，或先转换为 DXF。")
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        converted = output_dir / "converted.dxf"
        if converted.exists():
            raise FileExistsError("转换结果已存在，请选择空目录")
        with tempfile.TemporaryDirectory(prefix="dwg-input-", dir=output_dir) as tmp:
            copied = Path(tmp) / "input.dwg"
            shutil.copyfile(source, copied)
            run([str(converter), "-o", str(converted), str(copied)])
        if not converted.is_file() or converted.stat().st_size == 0:
            raise RuntimeError("DWG 转换器未生成有效输出")
        result["converted_dxf"] = str(converted)
        result["warnings"].append("DWG 已经转换为 DXF；转换可能丢失信息，需对照原图核验。")
        return read_dxf(converted, result, output_dir, preview_limit)
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
        from PIL import Image
        with Image.open(source) as image:
            if getattr(image, "n_frames", 1) != 1:
                raise ValueError("多页图片暂不支持，请先拆分为单页图片")
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                out = Path(output_dir).resolve()
                out.mkdir(parents=True, exist_ok=True)
                preview = out / "image.png"
                with preview.open("xb") as target:
                    image.convert("RGB").save(target, "PNG")
            else:
                image.load()
                preview = source
        result.update(ok=True, pages=1, images=[{"page": 1, "path": str(preview)}])
        result["warnings"].append("图片没有嵌入文字提取结果；本工具没有运行 OCR，需用视觉工具查看原图。")
        return result
    if suffix != ".pdf":
        raise ValueError("仅支持 PDF、DXF 和常见图片格式；DWG 需要先转换")
    programs = {name: shutil.which(name) for name in ("pdfinfo", "pdftotext", "pdftoppm")}
    missing = [name for name, path in programs.items() if not path]
    if missing:
        raise RuntimeError("缺少 PDF 工具: " + ", ".join(missing))
    info = run([programs["pdfinfo"], str(source)]).stdout.decode("utf-8", "replace")
    match = re.search(r"^Pages:\s*(\d+)", info, re.MULTILINE)
    if not match or int(match.group(1)) < 1:
        raise RuntimeError("无法读取 PDF 页数")
    count = int(match.group(1))
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if page is not None and not 1 <= page <= count:
        raise ValueError("请求的页码超出范围，图纸共 %d 页" % count)
    pages = [page] if page is not None else range(1, min(count, 3) + 1)
    existing = [output_dir / ("page-%04d.png" % p) for p in pages]
    if any(path.exists() for path in existing):
        raise FileExistsError("输出图片已存在，请选择空目录")
    result["pages"] = count
    if count > len(pages):
        result["warnings"].append("还有未读页；使用 --page N 查看其余页再判断图纸是否完整。")
    for page in pages:
        prefix = output_dir / ("page-%04d" % page)
        image = prefix.with_suffix(".png")
        run([programs["pdftoppm"], "-f", str(page), "-l", str(page), "-singlefile", "-r", "150", "-png", str(source), str(prefix)])
        if not image.is_file() or image.stat().st_size == 0:
            raise RuntimeError("PDF 页面渲染失败: " + str(page))
        text = run([programs["pdftotext"], "-f", str(page), "-l", str(page), "-layout", "-enc", "UTF-8", str(source), "-"]).stdout.decode("utf-8", "replace").strip("\f\n\r ")
        result["images"].append({"page": page, "path": str(image)})
        item = {"page": page, "source": "pdf_embedded_text", "text": text[:2400]}
        if len(text) > 2400:
            text_file = prefix.with_suffix(".txt")
            with text_file.open("x", encoding="utf-8") as full:
                full.write(text)
            item["full_text_path"] = str(text_file)
        result["extracted_text"].append(item)
        if not text:
            result["warnings"].append("第 %d 页没有可提取的嵌入文字；可能是扫描图，需查看页图。" % page)
    result["warnings"].append("未运行 OCR；嵌入文字可能缺字或顺序错误，不能单独确认尺寸。")
    result["ok"] = True
    return result


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--page", type=int, help="只读取指定 PDF 页（从 1 开始）；默认返回前 3 页")
    parser.add_argument("--output-dir", required=True, help="PDF 页面图片输出目录；已有同名页面不会覆盖")
    parser.add_argument("--dwg2dxf", help="LibreDWG dwg2dxf 可执行文件路径（不填则查找 PATH）")
    parser.add_argument("--preview-limit", type=int, default=3, help="DXF/DWG 预览最多的模型空间和布局数（0–20，默认 3）")
    args = parser.parse_args()
    try:
        if not 0 <= args.preview_limit <= 20:
            raise ValueError("--preview-limit 必须在 0 到 20 之间")
        result = prepare(args.input, args.output_dir, args.dwg2dxf, args.preview_limit, args.page)
    except (OSError, ValueError, RuntimeError, SyntaxError, ImportError, subprocess.SubprocessError) as exc:
        result = {"ok": False, "input": args.input, "error": str(exc), "ocr": "not_run"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
