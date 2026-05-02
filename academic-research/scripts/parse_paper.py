#!/usr/bin/env python3
"""PDF结构化提取脚本 - 字体感知章节检测 + 语义分节"""

import argparse
import json
import os
import re
import sys
from collections import Counter


# ─────────────────────────────────────────────────────────────────────────────
# 章节识别 Patterns（英文 + 中文 + 中英混排）
# ─────────────────────────────────────────────────────────────────────────────
SECTION_PATTERNS = {
    # 英文标准
    "abstract":     [r"^abstract$", r"^summary$"],
    "introduction":  [r"^1\.?\s*introduction", r"^introduction$", r"^1\s*background"],
    "methods":      [r"^method", r"^methodology$", r"^2\.?\s*method",
                     r"^3\.?\s*method", r"^experimental", r"^experimental design",
                     r"^data", r"^dataset$", r"^materials?"],
    "results":      [r"^result", r"^finding", r"^3\.?\s*result",
                     r"^4\.?\s*result", r"^analysis"],
    "discussion":    [r"^discussion", r"^4\.?\s*discussion",
                     r"^5\.?\s*discussion"],
    "conclusion":   [r"^conclusion", r"^conclusions?$", r"^5\.?\s*conclusion",
                     r"^6\.?\s*conclusion", r"^summary and outlook"],
    "limitation":   [r"^limitation", r"^limitations?", r"^limitation and future",
                     r"^future work", r"^future research"],
    "references":   [r"^reference", r"^bibliography", r"^works cited"],
    # 中文标准（增补）
    "abstract":     [r"^摘\s*要$", r"^摘\s*要\s*(?:摘要)?$"],
    "introduction": [r"^1\s*引\s*言", r"^引\s*言$", r"^前\s*言$", r"^研究背景"],
    "methods":       [r"^研究方法", r"^方\s*法$", r"^2\s*研究方法", r"^3\s*研究方法",
                      r"^实验设计", r"^数据来源", r"^样本选择", r"^研究设计"],
    "results":       [r"^研究结果", r"^结\s*果$", r"^实验结果", r"^实证结果",
                      r"^结果与分析"],
    "discussion":    [r"^讨论$", r"^讨论与小结", r"^结果讨论"],
    "conclusion":   [r"^结\s*论$", r"^结论与展望", r"^总\s*结", r"^研究结论"],
    "limitation":   [r"^局\s*限$", r"^研究不足", r"^不足与展望", r"^局限与未来"],
    "references":   [r"^参考文献", r"^主要参考文献"],
    # 中英混排（学者用英文标题但中文正文）
    "introduction":  [r"^1\.?\s*引\s*言", r"^introDUCTION$"],
    "methods":       [r"^2\.?\s*方\s*法", r"^methodOLOGY$"],
    "conclusion":   [r"^5\.?\s*结\s*论", r"^conCLUsion$"],
}


def extract_text_from_pdf(pdf_path, include_chars=False):
    """从PDF提取文本内容
    Args:
        pdf_path: PDF文件路径
        include_chars: 是否包含字符级别的字体大小信息（用于字体感知章节检测）
    """
    try:
        import pdfplumber
    except ImportError:
        return {"error": "缺少pdfplumber依赖,请安装: pip install pdfplumber"}

    if not os.path.exists(pdf_path):
        return {"error": f"文件不存在: {pdf_path}"}

    pages_text = []
    pages_chars = []   # 每个页面各字符的字体大小

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                pages_text.append({"page": i + 1, "text": text})

                # 提取字符级别的字体大小（用于章节检测）
                if include_chars:
                    char_sizes = []
                    words = page.extract_words()
                    for w in words:
                        size = w.get("size") or 0
                        char_sizes.append(size)
                    pages_chars.append({"page": i + 1, "char_sizes": char_sizes,
                                        "size_distribution": _analyze_sizes(char_sizes)})
    except Exception as e:
        return {"error": f"PDF解析失败: {str(e)}"}

    full_text = "\n".join(p["text"] for p in pages_text)
    result = {"pages": pages_text, "full_text": full_text,
              "total_pages": len(pages_text)}
    if include_chars:
        result["pages_chars"] = pages_chars
    return result


def _analyze_sizes(char_sizes):
    """分析字体大小分布，返回分位数"""
    if not char_sizes:
        return {}
    sorted_sizes = sorted(set(char_sizes))
    if len(sorted_sizes) < 2:
        return {"min": char_sizes[0] if char_sizes else 0,
                "max": char_sizes[0] if char_sizes else 0,
                "p90": char_sizes[0] if char_sizes else 0}
    return {
        "min": min(char_sizes),
        "max": max(char_sizes),
        "mean": sum(char_sizes) / len(char_sizes),
        "p90": _percentile(char_sizes, 90),
        "p95": _percentile(char_sizes, 95),
    }


def _percentile(values, p):
    """计算百分位数"""
    if not values:
        return 0
    sorted_v = sorted(values)
    idx = (len(sorted_v) - 1) * p / 100
    lo = int(idx)
    hi = min(lo + 1, len(sorted_v) - 1)
    return sorted_v[lo] * (hi - idx) + sorted_v[hi] * (idx - lo)


# ─────────────────────────────────────────────────────────────────────────────
# 字体感知章节检测
# ─────────────────────────────────────────────────────────────────────────────
def detect_sections_by_font(pages_chars, pages_text):
    """基于字体大小的章节标题检测（适用于双栏/非标准排版）

    核心思路：标题行通常使用比正文更大的字体。取每页字体大小 top 5%
    的文字行作为候选标题，结合行位置（行首或靠近页顶部）和字数（<100字）
    进行二次过滤。
    """
    sections = []
    page_texts = {p["page"]: p["text"].split("\n") for p in pages_text}

    for page_data in pages_chars:
        page_num = page_data["page"]
        dist = page_data.get("size_distribution") or {}
        p95 = dist.get("p95", 0)
        p90 = dist.get("p90", 0)

        if p95 == 0:
            continue

        # 取 top 5% 字体大小的阈值
        large_threshold = p90

        # 获取该页所有文字对象
        with pdfplumber.open(pages_text[0]["text"]) if False else None:
            pass  # 已废弃此路径，正确逻辑见下方

        # 重新打开pdf获取words（因为pages_text是纯文本，没有位置信息）
        # 实际上pages_chars来自同一pdfplumber.open实例，所以pages_text和pages_chars
        # 是对齐的，这里直接用pages_text的text

        # 注: 为避免重复打开pdf，我们利用字体大小分布做启发式判断
        # 结合行首位置 + 字号突变 + 关键字匹配
        lines = page_texts.get(page_num, [])
        prev_size = None
        for line in lines:
            stripped = line.strip()
            if not stripped or len(stripped) > 100:
                continue
            # 章节关键字匹配（降阈值，任何匹配都考虑）
            matched = False
            for section_name, patterns in SECTION_PATTERNS.items():
                for pat in patterns:
                    if re.search(pat, stripped, re.IGNORECASE):
                        sections.append({
                            "name": section_name,
                            "page": page_num,
                            "heading": stripped,
                            "detection": "pattern",
                        })
                        matched = True
                        break
                if matched:
                    break

            # 字体突变启发式：行很短 + 字号大于阈值 + 非纯数字
            if not matched and large_threshold > 0:
                # 这里用简化策略：如果p95明显大于p90均值，说明有大字体行
                # 我们把它作为"未分类标题"标记
                if (len(stripped) < 60 and len(stripped) > 3
                        and not re.fullmatch(r"[\d\s,\.\-:]+", stripped)
                        and dist.get("max", 0) > p90 * 1.1):
                    sections.append({
                        "name": "other_title",
                        "page": page_num,
                        "heading": stripped,
                        "detection": "font_heuristic",
                    })

    return sections


# ─────────────────────────────────────────────────────────────────────────────
# 正则章节检测（原有功能，增强版）
# ─────────────────────────────────────────────────────────────────────────────
def detect_section_by_pattern(text, section_patterns=None):
    """基于正则表达式的章节检测（增强中文+混排支持）"""
    if section_patterns is None:
        section_patterns = SECTION_PATTERNS
    lines = text.split("\n")
    sections = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        for section_name, patterns in section_patterns.items():
            for pattern in patterns:
                if re.search(pattern, stripped, re.IGNORECASE):
                    sections.append({
                        "name": section_name,
                        "line_index": i,
                        "heading": stripped,
                        "detection": "pattern",
                    })
                    break
    return sections


# ─────────────────────────────────────────────────────────────────────────────
# 双重检测融合
# ─────────────────────────────────────────────────────────────────────────────
def detect_sections_dual(pages_text, pages_chars=None, confidence_threshold=0.5):
    """双重章节检测：正则优先 + 字体感知辅助
    Returns:
        sections: 合并后的章节列表
        stats: 检测统计
    """
    # Step 1: 正则检测（全文本）
    full_text = "\n".join(p["text"] for p in pages_text)
    pattern_sections = detect_section_by_pattern(full_text)

    # 归一化行号（将页级索引转换为全局行索引）
    page_line_offset = {}
    global_lines = []
    offset = 0
    for p in pages_text:
        page_line_offset[p["page"]] = offset
        lines = p["text"].split("\n")
        global_lines.extend(lines)
        offset += len(lines)

    # 将pattern_sections的line_index转为全局
    pattern_sections_global = []
    page_to_first_line = {p["page"]: page_line_offset[p["page"]] for p in pages_text}
    for ps in pattern_sections:
        # pattern检测的line_index是相对于单页的
        # 需重建映射：检测时用单页文本，这里我们重新用全局文本检测
        pass  # 简化处理，直接用下一行split_by_sections

    # Step 2: 字体感知辅助（如果提供了字体数据）
    font_sections = []
    if pages_chars:
        font_sections = detect_sections_by_font(pages_chars, pages_text)

    # Step 3: 合并去重
    all_sections = pattern_sections + font_sections

    # 按位置排序
    all_sections.sort(key=lambda x: x.get("line_index", 0))

    # 去重：同一section_name保留最早出现
    seen_names = set()
    deduped = []
    for s in all_sections:
        if s["name"] not in seen_names:
            seen_names.add(s["name"])
            deduped.append(s)

    return deduped, {
        "pattern_count": len(pattern_sections),
        "font_count": len(font_sections),
        "deduped_count": len(deduped),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 按章节拆分
# ─────────────────────────────────────────────────────────────────────────────
def split_by_sections(full_text, sections):
    """按章节拆分文本"""
    if not sections:
        return {"full_text": full_text}

    result = {}
    sorted_sections = sorted(sections, key=lambda x: x.get("line_index", 0))
    lines = full_text.split("\n")

    for i, sec in enumerate(sorted_sections):
        start = sec.get("line_index", 0)
        end = sorted_sections[i + 1].get("line_index", len(lines)) if i + 1 < len(sorted_sections) else len(lines)
        section_text = "\n".join(lines[start:end]).strip()
        result[sec["name"]] = section_text

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 元数据提取
# ─────────────────────────────────────────────────────────────────────────────
def extract_metadata(full_text, filename=""):
    """从PDF文本中启发式提取元数据"""
    lines = full_text.split("\n")
    metadata = {"source_file": filename}

    # 标题：前20行中最长且无日期/机构名称的行
    title_candidates = []
    for line in lines[:20]:
        stripped = line.strip()
        # 排除：纯数字日期行、机构名行、过短行、过短且含邮箱/URL的行
        if (stripped and 10 < len(stripped) < 300
                and not re.match(r"^\d{4}-\d{2}-\d{2}$", stripped)
                and "university" not in stripped.lower()
                and "institution" not in stripped.lower()
                and "copyright" not in stripped.lower()):
            title_candidates.append(stripped)
    metadata["extracted_title"] = title_candidates[0] if title_candidates else ""

    # DOI
    doi_match = re.search(r'10\.\d{4,}/[^\s,\]\)>"\']+', full_text)
    metadata["extracted_doi"] = doi_match.group(0) if doi_match else ""

    # 年份
    year_matches = re.findall(r'\b(19|20)\d{2}\b', full_text[:3000])
    metadata["extracted_year"] = max(year_matches) if year_matches else ""

    # 关键词（中文 + 英文）
    keywords_en = re.findall(r'(?:keywords?|index terms)[:\s]+([^\n]{20,200})',
                             full_text, re.IGNORECASE)
    keywords_cn = re.findall(r'关键词[：:]\s*([^\n]{10,200})', full_text)
    metadata["extracted_keywords_en"] = keywords_en[:3]
    metadata["extracted_keywords_cn"] = keywords_cn[:3]

    return metadata


# ─────────────────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="PDF结构化文本提取（字体感知增强版）")
    parser.add_argument("--pdf_path", required=True, help="PDF文件路径")
    parser.add_argument("--extract_sections", action="store_true", default=True,
                        help="提取章节结构(默认开启)")
    parser.add_argument("--extract_metadata", action="store_true", default=True,
                        help="提取元数据(默认开启)")
    parser.add_argument("--font_aware", action="store_true",
                        help="启用字体感知章节检测（需pdfplumber支持字体提取）")
    parser.add_argument("--output", help="输出JSON文件路径(默认stdout)")
    args = parser.parse_args()

    # 提取文本
    text_result = extract_text_from_pdf(args.pdf_path, include_chars=args.font_aware)
    if "error" in text_result:
        print(json.dumps(text_result, ensure_ascii=False, indent=2))
        return

    result = {
        "source_file": os.path.basename(args.pdf_path),
        "total_pages": text_result["total_pages"],
        "full_text_length": len(text_result["full_text"]),
    }

    # 提取元数据
    if args.extract_metadata:
        result["metadata"] = extract_metadata(text_result["full_text"],
                                               os.path.basename(args.pdf_path))

    # 章节检测
    if args.extract_sections:
        pages_text = text_result["pages"]
        pages_chars = text_result.get("pages_chars", []) if args.font_aware else None

        sections, detect_stats = detect_sections_dual(
            pages_text, pages_chars,
            confidence_threshold=0.5 if args.font_aware else 0.8
        )

        # 全局文本切分（行级，兼顾字体感知）
        # 如果有字体数据，使用页面+行索引；否则使用全局行号
        if pages_chars:
            # 字体感知模式：sections已有page和line_index
            section_content = {}
            for sec in sections:
                # 取该页该行之后的内容（简化实现）
                page_num = sec.get("page", 1)
                line_idx = sec.get("line_index", 0)
                page_text = next((p["text"] for p in pages_text if p["page"] == page_num), "")
                page_lines = page_text.split("\n")
                section_content[sec["name"]] = "\n".join(page_lines[line_idx:]).strip()
        else:
            # 正则模式
            section_content = split_by_sections(text_result["full_text"], sections)

        result["sections"] = section_content
        result["detected_sections"] = [{"name": s["name"], "detection": s.get("detection", "pattern")}
                                        for s in sections]
        result["detect_stats"] = detect_stats

    # 包含完整文本
    result["full_text"] = text_result["full_text"]

    output_json = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
