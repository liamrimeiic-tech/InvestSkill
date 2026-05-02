#!/usr/bin/env python3
"""Zotero同步脚本 - 文献创建/笔记注入/附件重命名/云端同步"""

import argparse
import json
import os
import sys
import re
import base64

from coze_workload_identity import requests

SKILL_ID = "7635302993344233472"

ZOTERO_API_BASE = "https://api.zotero.org"


def _esc(text):
    """HTML转义，防止特殊字符破坏HTML结构"""
    if not text:
        return ""
    return (text.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def get_credential():
    """获取Zotero API凭证"""
    api_key = os.getenv(f"COZE_ZOTERO_API_{SKILL_ID}")
    if not api_key:
        raise ValueError("缺少Zotero API凭证,请先配置zotero_api凭证")
    return api_key


def get_headers(api_key):
    """构建请求头"""
    return {
        "Zotero-API-Key": api_key,
        "Content-Type": "application/json",
    }


def create_item(library_id, library_type, item_data, api_key):
    """在Zotero中创建文献条目"""
    url = f"{ZOTERO_API_BASE}/{library_type}s/{library_id}/items"
    headers = get_headers(api_key)

    # 构建Zotero条目格式
    zotero_item = {
        "itemType": "journalArticle",
        "title": item_data.get("title", ""),
        "creators": [
            {"creatorType": "author", "firstName": name.split(" ", 1)[0] if " " in name else "",
             "lastName": name.split(" ", 1)[-1] if " " in name else name}
            for name in item_data.get("authors", [])
        ],
        "abstractNote": item_data.get("abstract", ""),
        "date": str(item_data.get("year", "")),
        "DOI": item_data.get("doi", ""),
        "url": item_data.get("url", ""),
        "extra": f"Semantic Scholar ID: {item_data.get('paper_id', '')}\n"
                 f"Citations: {item_data.get('citation_count', 0)}\n"
                 f"Track: {item_data.get('track', 'N/A')}\n"
                 f"Relevance Score: {item_data.get('_relevance_score', 'N/A')}",
        "tags": [
            {"tag": tag} for tag in item_data.get("fields_of_study", [])
        ] + [
            {"tag": f"track:{item_data.get('track', 'unknown')}"},
        ],
    }

    payload = [zotero_item]

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code not in (200, 201):
            return {"error": f"创建条目失败: HTTP {resp.status_code}", "detail": resp.text[:500]}
        data = resp.json()
        successful = data.get("successful", {})
        if successful:
            item_key = list(successful.values())[0].get("key", "")
            return {"status": "success", "item_key": item_key, "data": dict(successful)}
        else:
            failed = data.get("failed", {})
            return {"error": "创建失败", "detail": json.dumps(failed, ensure_ascii=False)}
    except Exception as e:
        return {"error": f"请求异常: {str(e)}"}


def add_note(library_id, library_type, item_key, note_content, api_key):
    """向Zotero条目添加笔记(深度内容参数)"""
    url = f"{ZOTERO_API_BASE}/{library_type}s/{library_id}/items"
    headers = get_headers(api_key)

    note_item = {
        "itemType": "note",
        "parentItem": item_key,
        "note": note_content,
        "tags": [{"tag": "深度解析"}],
    }

    payload = [note_item]

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code not in (200, 201):
            return {"error": f"添加笔记失败: HTTP {resp.status_code}", "detail": resp.text[:500]}
        data = resp.json()
        successful = data.get("successful", {})
        if successful:
            note_key = list(successful.values())[0].get("key", "")
            return {"status": "success", "note_key": note_key}
        else:
            return {"error": "添加笔记失败", "detail": json.dumps(data.get("failed", {}), ensure_ascii=False)}
    except Exception as e:
        return {"error": f"请求异常: {str(e)}"}


def rename_attachment(library_id, library_type, item_key, new_filename, api_key):
    """重命名Zotero附件"""
    url = f"{ZOTERO_API_BASE}/{library_type}s/{library_id}/items/{item_key}"
    headers = get_headers(api_key)

    # 先获取当前附件信息
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return {"error": f"获取附件信息失败: HTTP {resp.status_code}"}
        item_data = resp.json()
        item_data["title"] = new_filename
        item_data["filename"] = new_filename

        # 更新条目
        resp = requests.patch(url, headers=headers, json=item_data, timeout=15)
        if resp.status_code != 200:
            return {"error": f"重命名失败: HTTP {resp.status_code}", "detail": resp.text[:500]}
        return {"status": "success", "new_filename": new_filename}
    except Exception as e:
        return {"error": f"请求异常: {str(e)}"}


def format_rename(paper_data):
    """生成标准重命名格式: [轨道] 年份-第一作者-标题.pdf
    轨道标识: [Classic] = 经典轨, [Frontier] = 前沿轨, 无前缀 = 通用
    """
    track = paper_data.get("track", "")
    track_prefix = ""
    if track == "classic":
        track_prefix = "[Classic] "
    elif track == "frontier":
        track_prefix = "[Frontier] "
    # 优先从解析结果中提取年份，否则从元数据
    year = paper_data.get("year", "unknown")
    authors = paper_data.get("authors", [])
    first_author = authors[0].split(" ")[-1] if authors else "unknown"
    title = paper_data.get("title", "untitled")
    # 清理标题中的特殊字符，保留常见标点
    clean_title = "".join(
        c for c in title if c.isalnum() or c in " -_.,()[]").strip()
    clean_title = clean_title[:80]
    return f"{track_prefix}{year}-{first_author}-{clean_title}.pdf"


def build_extra_tags(parsed_content, paper_data):
    """从深度解析内容中提取结构化参数，注入Zotero Extra字段
    格式: key:value; key:value; ...
    支持Zotero搜索框直接检索: N=2000, method: randomized 等
    """
    tags = []
    sections = parsed_content.get("sections", {})
    metadata = parsed_content.get("metadata", {})
    full_text = parsed_content.get("full_text", "")

    # 从全文或摘要中提取样本量
    for pattern in [
        r'[Nn]=(\d{3,7})',
        r'sample of (\d{3,7})',
        r'(\d{1,3}(?:,\d{3})*) (?:observations?|observations)',
        r'样本量[为:]?\s*(\d{3,7})',
        r'样本\s*[为:]?\s*(\d{3,7})',
    ]:
        m = re.search(pattern, full_text)
        if m:
            tags.append(f"sample:{m.group(1)}")
            break

    # 从methods节中提取研究方法关键词
    methods_text = sections.get("methods", "")
    for method_kw in [
        "randomized", "RCT", "diff-in-diff", "difference-in-difference",
        "synthetic control", "regression discontinuity", "instrumental variable",
        "panel data", "survival analysis", "deep learning", "neural network",
        "reinforcement learning", "machine learning", "propensity score",
        "structural model", "general equilibrium", "field experiment",
        "causal inference", "granger causality", "VAR", "GARCH",
        "quantitative", "qualitative", "mixed methods",
        "随机对照", "双重差分", "工具变量", "断点回归", "面板数据",
    ]:
        if method_kw.lower() in (methods_text + paper_data.get("title", "")).lower():
            tags.append(f"method:{method_kw}")
            break

    # 从limitation节中提取局限性标签
    limitation_text = sections.get("limitation", "")
    for lim_kw in [
        "endogeneity", "selection bias", "causality", "generalizability",
        "external validity", "internal validity", "measurement error",
        "confounding", " omitted variable",
        "内生性", "选择性偏差", "外部有效性", "测量误差",
    ]:
        if lim_kw.lower() in limitation_text.lower():
            tags.append(f"limitation:{lim_kw}")
            break

    # 筛选轨道
    track = paper_data.get("track", "")
    if track:
        tags.append(f"track:{track}")

    # 领域
    fields = paper_data.get("fields_of_study", [])
    for field in fields[:2]:
        tags.append(f"field:{field}")

    # 唯一化
    seen = set()
    unique_tags = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            unique_tags.append(t)
    return "; ".join(unique_tags)


def build_comparison_note(current_paper, other_papers):
    """构建跨文献对比笔记，注入本文的Zotero Notes

    Args:
        current_paper: 当前论文的paper_data
        other_papers: 同session中其他已解析论文列表

    Returns:
        HTML格式的对比笔记字符串
    """
    if not other_papers:
        return ""

    current_id = current_paper.get("paper_id") or current_paper.get("doi", "")
    current_title = current_paper.get("title", "")[:60]
    current_year = current_paper.get("year", "")
    current_track = current_paper.get("track", "")
    current_methods = current_paper.get("_parsed_methods", "")

    lines = [
        '<h2>跨文献对比</h2>',
        '<p>本文与同课题其他文献的方法论与结论对比：</p>',
        '<table>',
        '<tr><th>对比维度</th><th>本文</th><th>对比文献</th></tr>',
    ]

    # 维度1: 研究方法
    method_dim = f'<b>方法</b>: {current_methods or "见研究方法节"}'
    for other in other_papers[:3]:
        other_id = other.get("paper_id") or other.get("doi", "")
        if other_id == current_id:
            continue
        other_title = other.get("title", "")[:50]
        other_year = other.get("year", "")
        other_methods = other.get("_parsed_methods", "未解析")
        other_track = other.get("track", "")
        rows = [
            ("主题", current_title[:50], f'{other_title[:50]} ({other_year})'),
            ("轨道", current_track, other_track),
            ("方法", current_methods or "见节", other_methods or "见节"),
        ]
        for dim, cur, oth in rows:
            lines.append(f'<tr><td><b>{dim}</b></td><td>{cur}</td><td>{oth}</td></tr>')
        break  # 仅对比最相关的1篇
    lines.append('</table>')

    lines.append('<h3>方法论差异简述</h3>')
    for other in other_papers[:3]:
        other_id = other.get("paper_id") or other.get("doi", "")
        if other_id == current_id:
            continue
        other_title = other.get("title", "")[:50]
        cur_methods = current_paper.get("_parsed_methods", "未提取")
        oth_methods = other.get("_parsed_methods", "未提取")
        lines.append(
            f'<p>[对比 {current_id} vs {other_id}] '
            f'本文({cur_methods}) vs {other_title}({oth_methods})</p>'
        )

    return "\n".join(lines)


def build_note_html(parsed_content, paper_data):
    """构建Zotero笔记HTML内容（深度内容 + 智能体分析 + 跨文献对比占位）

    智能体分析区域在笔记中保留占位结构，填充内容通过session_manager的
    cross_paper_comparison字段注入，Notes字段由智能体追加补充。
    """
    metadata = parsed_content.get("metadata", {})
    sections = parsed_content.get("sections", {})
    full_text = parsed_content.get("full_text", "")

    html_parts = [
        '<h1>深度内容解析</h1>',
        '<h2>元数据</h2>',
        '<table>',
        f'<tr><td><b>标题</b></td><td>{_esc(paper_data.get("title", ""))}</td></tr>',
        f'<tr><td><b>年份</b></td><td>{paper_data.get("year", "")}</td></tr>',
        f'<tr><td><b>作者</b></td><td>{", ".join(paper_data.get("authors", []))}</td></tr>',
        f'<tr><td><b>引用量</b></td><td>{paper_data.get("citation_count", 0)}</td></tr>',
        f'<tr><td><b>DOI</b></td><td>{paper_data.get("doi", "")}</td></tr>',
        f'<tr><td><b>筛选轨道</b></td><td>{paper_data.get("track", "N/A")}</td></tr>',
        f'<tr><td><b>语义相关性</b></td><td>{paper_data.get("_relevance_score", "N/A")}</td></tr>',
        '</table>',
    ]

    if sections:
        html_parts.append('<h2>研究方法</h2>')
        html_parts.append(f'<p>{_esc(sections.get("methods", "未能自动提取"))}</p>')
        html_parts.append('<h2>核心结论</h2>')
        html_parts.append(f'<p>{_esc(sections.get("conclusion", sections.get("results", "未能自动提取")))}</p>')
        html_parts.append('<h2>局限性</h2>')
        html_parts.append(f'<p>{_esc(sections.get("limitation", "未能自动提取"))}</p>')

    # 自动提取参数（从解析内容中挖掘）
    auto_sample = None
    for pattern in [r'[Nn]=(\d{3,7})', r'sample of (\d{3,7})',
                    r'样本量[为:]?\s*(\d{3,7})']:
        m = re.search(pattern, full_text)
        if m:
            auto_sample = f"N={m.group(1)}"
            break

    html_parts.extend([
        '<h2>智能体分析</h2>',
        '<p><b>样本规模:</b> ' + (auto_sample if auto_sample else '[由智能体在解析后填充]') + '</p>',
        '<p><b>方法论评估:</b> [由智能体基于全文综合分析后补充]</p>',
        '<p><b>核心贡献:</b> [由智能体提炼后补充]</p>',
        '<p><b>局限性评估:</b> [由智能体补充]</p>',
        '<h2>跨文献对比</h2>',
        '<p>[由智能体在同课题其他文献解析完成后追加，'
        '格式: 本文 vs Gorton(2017): 方法论差异描述]</p>',
    ])

    return "\n".join(html_parts)


def list_items(library_id, library_type, api_key, limit=25, start=0):
    """列出Zotero库中的条目"""
    url = f"{ZOTERO_API_BASE}/{library_type}s/{library_id}/items"
    headers = get_headers(api_key)
    params = {"limit": limit, "start": start, "format": "json"}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            return {"error": f"获取条目列表失败: HTTP {resp.status_code}"}
        items = resp.json()
        return {
            "status": "success",
            "count": len(items),
            "items": [
                {
                    "key": item.get("key", ""),
                    "title": item.get("data", {}).get("title", ""),
                    "item_type": item.get("data", {}).get("itemType", ""),
                    "date": item.get("data", {}).get("date", ""),
                }
                for item in items
            ],
        }
    except Exception as e:
        return {"error": f"请求异常: {str(e)}"}


def main():
    parser = argparse.ArgumentParser(description="Zotero文献管理与同步")
    parser.add_argument("--operation", required=True,
                        choices=["create_item", "add_note", "rename_attachment",
                                 "build_note", "list_items", "batch_create",
                                 "build_extra_tags", "build_comparison"],
                        help="操作类型")
    parser.add_argument("--library_id", required=True, help="Zotero Library ID")
    parser.add_argument("--library_type", choices=["user", "group"], default="user",
                        help="库类型(user/group)")
    # create_item / batch_create
    parser.add_argument("--item_data", help="文献数据JSON字符串")
    parser.add_argument("--items_file", help="批量文献数据JSON文件路径")
    # add_note
    parser.add_argument("--item_key", help="Zotero条目Key")
    parser.add_argument("--note_content", help="笔记内容(HTML)")
    # rename
    parser.add_argument("--new_filename", help="新文件名")
    # list
    parser.add_argument("--limit", type=int, default=25, help="列表返回数量")
    # build_note
    parser.add_argument("--parsed_content", help="解析内容JSON字符串")
    parser.add_argument("--paper_data", help="论文元数据JSON字符串")
    args = parser.parse_args()

    # 本地操作（无需API凭证）
    if args.operation == "build_note":
        parsed = json.loads(args.parsed_content) if args.parsed_content else {}
        pdata = json.loads(args.paper_data) if args.paper_data else {}
        note_html = build_note_html(parsed, pdata)
        rename = format_rename(pdata)
        result = {"note_html": note_html, "suggested_filename": rename}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.operation == "build_extra_tags":
        parsed = json.loads(args.parsed_content) if args.parsed_content else {}
        pdata = json.loads(args.paper_data) if args.paper_data else {}
        result = {"extra_tags": build_extra_tags(parsed, pdata)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.operation == "build_comparison":
        pdata = json.loads(args.paper_data) if args.paper_data else {}
        other_papers = json.loads(args.parsed_content) if args.parsed_content else []
        result = {"comparison_html": build_comparison_note(pdata, other_papers)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 其余操作需要API凭证
    try:
        api_key = get_credential()
    except ValueError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False, indent=2))
        return

    if args.operation == "create_item":
        item_data = json.loads(args.item_data) if args.item_data else {}
        result = create_item(args.library_id, args.library_type, item_data, api_key)

    elif args.operation == "batch_create":
        if args.items_file:
            with open(args.items_file, "r", encoding="utf-8") as f:
                items = json.load(f)
        elif args.item_data:
            items = json.loads(args.item_data)
        else:
            result = {"error": "批量创建需提供 --items_file 或 --item_data"}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

        results = []
        for item in items:
            r = create_item(args.library_id, args.library_type, item, api_key)
            r["title"] = item.get("title", "")
            results.append(r)
        result = {"status": "batch_complete", "results": results,
                  "success_count": sum(1 for r in results if r.get("status") == "success"),
                  "fail_count": sum(1 for r in results if "error" in r)}

    elif args.operation == "add_note":
        if not args.item_key or not args.note_content:
            result = {"error": "add_note需要 --item_key 和 --note_content"}
        else:
            result = add_note(args.library_id, args.library_type, args.item_key,
                            args.note_content, api_key)

    elif args.operation == "rename_attachment":
        if not args.item_key:
            result = {"error": "rename_attachment需要 --item_key"}
        elif not args.new_filename:
            result = {"error": "rename_attachment需要 --new_filename"}
        else:
            result = rename_attachment(args.library_id, args.library_type,
                                      args.item_key, args.new_filename, api_key)

    elif args.operation == "list_items":
        result = list_items(args.library_id, args.library_type, api_key, args.limit)

    else:
        result = {"error": f"未知操作: {args.operation}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
