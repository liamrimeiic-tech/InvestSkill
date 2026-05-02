#!/usr/bin/env python3
"""Semantic Scholar学术文献检索脚本 - 支持关键词检索与元数据提取"""

import argparse
import json
import os
import sys
from coze_workload_identity import requests

SKILL_ID = "7635302993344233472"
BASE_URL = "https://api.semanticscholar.org/graph/v1"

FIELDS = "title,authors,year,abstract,citationCount,referenceCount,url,externalIds,openAccessPdf,fieldsOfStudy,publicationDate,citationStyles"


def search_papers(query, year_from=None, year_to=None, limit=20, offset=0):
    """搜索Semantic Scholar论文"""
    url = f"{BASE_URL}/paper/search"
    params = {
        "query": query,
        "limit": min(limit, 100),
        "offset": offset,
        "fields": FIELDS,
    }
    if year_from or year_to:
        year_range = f"{year_from or ''}-{year_to or ''}"
        params["year"] = year_range

    api_key = os.getenv(f"COZE_SEMANTIC_SCHOLAR_API_{SKILL_ID}")
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code == 429:
            return {"error": "API速率限制，请稍后重试", "retry_after": 60}
        if resp.status_code != 200:
            return {"error": f"API请求失败: HTTP {resp.status_code}", "detail": resp.text[:500]}
        data = resp.json()
    except Exception as e:
        return {"error": f"请求异常: {str(e)}"}

    papers = []
    for p in data.get("data", []):
        authors = [a.get("name", "") for a in p.get("authors", []) if a.get("name")]
        ext_ids = p.get("externalIds", {}) or {}
        oa_pdf = p.get("openAccessPdf")
        papers.append({
            "paper_id": p.get("paperId", ""),
            "title": p.get("title", ""),
            "authors": authors,
            "year": p.get("year"),
            "publication_date": p.get("publicationDate", ""),
            "abstract": p.get("abstract", "") or "",
            "citation_count": p.get("citationCount", 0) or 0,
            "reference_count": p.get("referenceCount", 0) or 0,
            "url": p.get("url", ""),
            "doi": ext_ids.get("DOI", ""),
            "arxiv_id": ext_ids.get("ArXiv", ""),
            "open_access_pdf": oa_pdf.get("url", "") if oa_pdf else "",
            "fields_of_study": p.get("fieldsOfStudy") or [],
            "source": "semantic_scholar",
        })

    return {
        "total": data.get("total", 0),
        "offset": data.get("offset", 0),
        "next_offset": offset + len(papers),
        "has_more": data.get("total", 0) > offset + len(papers),
        "papers": papers,
    }


def search_by_doi(doi):
    """通过DOI精确检索单篇论文"""
    url = f"{BASE_URL}/paper/DOI:{doi}"
    params = {"fields": FIELDS}

    api_key = os.getenv(f"COZE_SEMANTIC_SCHOLAR_API_{SKILL_ID}")
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code == 404:
            return {"error": f"未找到DOI: {doi}"}
        if resp.status_code != 200:
            return {"error": f"API请求失败: HTTP {resp.status_code}"}
        p = resp.json()
    except Exception as e:
        return {"error": f"请求异常: {str(e)}"}

    authors = [a.get("name", "") for a in p.get("authors", []) if a.get("name")]
    ext_ids = p.get("externalIds", {}) or {}
    oa_pdf = p.get("openAccessPdf")
    paper = {
        "paper_id": p.get("paperId", ""),
        "title": p.get("title", ""),
        "authors": authors,
        "year": p.get("year"),
        "publication_date": p.get("publicationDate", ""),
        "abstract": p.get("abstract", "") or "",
        "citation_count": p.get("citationCount", 0) or 0,
        "reference_count": p.get("referenceCount", 0) or 0,
        "url": p.get("url", ""),
        "doi": ext_ids.get("DOI", ""),
        "arxiv_id": ext_ids.get("ArXiv", ""),
        "open_access_pdf": oa_pdf.get("url", "") if oa_pdf else "",
        "fields_of_study": p.get("fieldsOfStudy") or [],
        "source": "semantic_scholar",
    }
    return {"total": 1, "papers": [paper]}


def batch_search(queries, year_from=None, year_to=None, limit_per_query=10):
    """多关键词批量检索并去重"""
    seen_ids = set()
    all_papers = []
    for q in queries:
        result = search_papers(q, year_from, year_to, limit_per_query)
        if "error" in result:
            continue
        for p in result.get("papers", []):
            pid = p["paper_id"]
            if pid not in seen_ids:
                seen_ids.add(pid)
                all_papers.append(p)
    return {"total": len(all_papers), "papers": all_papers}


# ─────────────────────────────────────────────────────────────────────────────
# 引用追溯（Reference Snowballing）
# 从确认文献的参考文献列表中，自动发现高频被引的"共引文献"
# 核心逻辑：若某文献被多篇确认文献同时引用，说明它是领域基石
# ─────────────────────────────────────────────────────────────────────────────
def get_paper_references(paper_id, limit=50):
    """获取某篇论文的参考文献列表"""
    url = f"{BASE_URL}/paper/{paper_id}/references"
    params = {
        "fields": "title,authors,year,citationCount,externalIds,abstract,openAccessPdf,fieldsOfStudy",
        "limit": min(limit, 100),
    }
    api_key = os.getenv(f"COZE_SEMANTIC_SCHOLAR_API_{SKILL_ID}")
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code != 200:
            return {"error": f"获取参考文献失败: HTTP {resp.status_code}"}
        data = resp.json()
    except Exception as e:
        return {"error": f"请求异常: {str(e)}"}

    refs = []
    for r in data.get("data", []):
        cited = r.get("citedPaper", {}) or {}
        if not cited.get("paperId"):
            continue
        authors = [a.get("name", "") for a in cited.get("authors", []) if a.get("name")]
        ext_ids = cited.get("externalIds") or {}
        oa_pdf = cited.get("openAccessPdf")
        refs.append({
            "paper_id": cited.get("paperId", ""),
            "title": cited.get("title", ""),
            "authors": authors,
            "year": cited.get("year"),
            "citation_count": cited.get("citationCount", 0) or 0,
            "doi": ext_ids.get("DOI", ""),
            "open_access_pdf": oa_pdf.get("url", "") if oa_pdf else "",
            "abstract": cited.get("abstract", "") or "",
            "fields_of_study": cited.get("fieldsOfStudy") or [],
            "source": "semantic_scholar",
            "referenced_by": [paper_id],  # 标记被哪些确认文献引用
        })
    return {"paper_id": paper_id, "references": refs}


def get_paper_citations(paper_id, limit=50):
    """获取某篇论文的引用列表（被哪些论文引用）"""
    url = f"{BASE_URL}/paper/{paper_id}/citations"
    params = {
        "fields": "title,authors,year,citationCount,externalIds,abstract,openAccessPdf,fieldsOfStudy",
        "limit": min(limit, 100),
    }
    api_key = os.getenv(f"COZE_SEMANTIC_SCHOLAR_API_{SKILL_ID}")
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code != 200:
            return {"error": f"获取引用列表失败: HTTP {resp.status_code}"}
        data = resp.json()
    except Exception as e:
        return {"error": f"请求异常: {str(e)}"}

    cits = []
    for c in data.get("data", []):
        citing = c.get("citingPaper", {}) or {}
        if not citing.get("paperId"):
            continue
        authors = [a.get("name", "") for a in citing.get("authors", []) if a.get("name")]
        ext_ids = citing.get("externalIds") or {}
        oa_pdf = citing.get("openAccessPdf")
        cits.append({
            "paper_id": citing.get("paperId", ""),
            "title": citing.get("title", ""),
            "authors": authors,
            "year": citing.get("year"),
            "citation_count": citing.get("citationCount", 0) or 0,
            "doi": ext_ids.get("DOI", ""),
            "open_access_pdf": oa_pdf.get("url", "") if oa_pdf else "",
            "abstract": citing.get("abstract", "") or "",
            "fields_of_study": citing.get("fieldsOfStudy") or [],
            "source": "semantic_scholar",
        })
    return {"paper_id": paper_id, "citations": cits}


def snowball_references(paper_ids, min_co_cite_count=2, ref_limit=30):
    """引用追溯：从多篇确认文献中找出高频共引的参考文献

    Args:
        paper_ids: 确认文献的paper_id列表
        min_co_cite_count: 最低共引次数（至少被多少篇确认文献引用）
        ref_limit: 每篇确认文献最多获取多少条参考文献

    Returns:
        共引频次 >= min_co_cite_count 的文献列表，按共引次数降序
    """
    from collections import defaultdict
    co_cite_count = defaultdict(int)   # {paper_id: 被共引次数}
    co_cite_info = {}                  # {paper_id: paper_data}

    for pid in paper_ids:
        result = get_paper_references(pid, limit=ref_limit)
        if "error" in result:
            continue
        for ref in result.get("references", []):
            ref_id = ref["paper_id"]
            if ref_id in co_cite_count:
                # 已存在，增加共引计数
                co_cite_count[ref_id] += 1
                # 更新referenced_by列表
                co_cite_info[ref_id].setdefault("referenced_by", []).append(pid)
            else:
                co_cite_count[ref_id] = 1
                co_cite_info[ref_id] = ref

    # 筛选共引次数达标的文献
    snowball_papers = []
    for ref_id, count in co_cite_count.items():
        if count >= min_co_cite_count:
            paper = co_cite_info[ref_id]
            paper["co_cite_count"] = count
            snowball_papers.append(paper)

    # 按共引次数降序
    snowball_papers.sort(key=lambda x: x["co_cite_count"], reverse=True)
    return snowball_papers


def main():
    parser = argparse.ArgumentParser(description="学术文献检索 - Semantic Scholar（含引用追溯）")
    parser.add_argument("--query", help="检索关键词")
    parser.add_argument("--doi", help="通过DOI精确检索")
    parser.add_argument("--queries", help="多关键词批量检索(JSON数组字符串)")
    parser.add_argument("--year_from", type=int, help="起始年份")
    parser.add_argument("--year_to", type=int, help="截止年份")
    parser.add_argument("--limit", type=int, default=20, help="最大返回数量(单次上限100)")
    parser.add_argument("--offset", type=int, default=0, help="分页偏移量")
    # 引用追溯参数
    parser.add_argument("--snowball", action="store_true",
                        help="启用引用追溯模式")
    parser.add_argument("--paper_ids", help="确认文献的paper_id列表(JSON数组字符串)")
    parser.add_argument("--min_co_cite", type=int, default=2,
                        help="最低共引次数(默认2)")
    parser.add_argument("--get_references", help="获取指定paper_id的参考文献")
    parser.add_argument("--get_citations", help="获取指定paper_id的引用列表")
    args = parser.parse_args()

    if args.get_references:
        result = get_paper_references(args.get_references, args.limit)
    elif args.get_citations:
        result = get_paper_citations(args.get_citations, args.limit)
    elif args.snowball and args.paper_ids:
        paper_ids = json.loads(args.paper_ids)
        papers = snowball_references(paper_ids, min_co_cite_count=args.min_co_cite,
                                    ref_limit=args.limit)
        result = {"total": len(papers), "papers": papers}
    elif args.doi:
        result = search_by_doi(args.doi)
    elif args.queries:
        queries = json.loads(args.queries)
        result = batch_search(queries, args.year_from, args.year_to, args.limit)
    elif args.query:
        result = search_papers(args.query, args.year_from, args.year_to, args.limit, args.offset)
    else:
        result = {"error": "请提供 --query, --doi, --queries, --snowball 或 --get_references 参数"}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
