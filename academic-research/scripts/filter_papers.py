#!/usr/bin/env python3
"""双轨筛选 + TF-IDF语义权重过滤脚本
- 经典轨: 引用量Top 5%的基石研究
- 前沿轨: 近2-3年发表且引用斜率陡峭的研究
- 语义权重: TF-IDF计算摘要与主题的相关性
"""

import argparse
import json
import math
import sys
from datetime import datetime

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    import numpy as np
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


def compute_citation_velocity(paper, current_year=None):
    """计算引用速度(引用量/年)"""
    if not current_year:
        current_year = datetime.now().year
    year = paper.get("year")
    if not year or year <= 0:
        return 0.0
    years_since = max(current_year - year, 1)
    return paper.get("citation_count", 0) / years_since


def compute_citation_slope(paper, current_year=None):
    """估算引用斜率(近期引用加速度)
    使用启发式: 近期引用比例 × 总引用速度
    假设论文的引用增长符合S曲线，近2年引用占总引用的比例越高则斜率越陡
    """
    if not current_year:
        current_year = datetime.now().year
    year = paper.get("year")
    if not year or year <= 0:
        return 0.0
    years_since = current_year - year
    if years_since <= 0:
        return float(paper.get("citation_count", 0))

    citation_count = paper.get("citation_count", 0)
    velocity = citation_count / years_since

    # 近期加速度启发式: 论文越新且引用越高，斜率越陡
    # 使用指数衰减模型: 斜率 = velocity * (1 + recency_boost)
    recency_boost = math.exp(-0.3 * (years_since - 1)) if years_since <= 5 else 0.1
    return velocity * (1 + recency_boost)


def dual_track_filter(papers, classic_percentile=95, frontier_years=3,
                      frontier_slope_percentile=70, current_year=None):
    """双轨筛选
    Args:
        papers: 论文列表
        classic_percentile: 经典轨百分位阈值(默认95即Top 5%)
        frontier_years: 前沿轨年份窗口(默认3年)
        frontier_slope_percentile: 前沿轨斜率百分位阈值
        current_year: 当前年份
    Returns:
        dict: {"classic": [...], "frontier": [...], "stats": {...}}
    """
    if not current_year:
        current_year = datetime.now().year

    if not papers:
        return {"classic": [], "frontier": [], "stats": {"total_input": 0}}

    # 计算每篇论文的引用斜率
    for p in papers:
        p["_citation_velocity"] = compute_citation_velocity(p, current_year)
        p["_citation_slope"] = compute_citation_slope(p, current_year)

    # === 经典轨 ===
    citation_counts = [p.get("citation_count", 0) for p in papers]
    classic_threshold = np.percentile(citation_counts, classic_percentile) if HAS_SKLEARN and len(citation_counts) >= 5 else sorted(citation_counts)[max(0, len(citation_counts) - max(1, len(citation_counts) // 20))]
    classic_papers = [p for p in papers if p.get("citation_count", 0) >= classic_threshold]

    # === 前沿轨 ===
    frontier_cutoff_year = current_year - frontier_years
    recent_papers = [p for p in papers if p.get("year") and p["year"] >= frontier_cutoff_year]

    if recent_papers:
        slopes = [p["_citation_slope"] for p in recent_papers]
        if HAS_SKLEARN and len(slopes) >= 5:
            slope_threshold = np.percentile(slopes, frontier_slope_percentile)
        else:
            sorted_slopes = sorted(slopes, reverse=True)
            slope_threshold = sorted_slopes[max(0, min(len(sorted_slopes) - 1, len(sorted_slopes) * frontier_slope_percentile // 100))]
        frontier_papers = [p for p in recent_papers if p["_citation_slope"] >= slope_threshold]
    else:
        frontier_papers = []

    # 记录斜率阈值(在清理前)
    saved_slope_threshold = slope_threshold if recent_papers else 0

    # 去重: 同一篇论文可能同时出现在两个轨道
    classic_ids = {p.get("paper_id") or p.get("doi") or p.get("title") for p in classic_papers}
    frontier_only = [p for p in frontier_papers
                     if (p.get("paper_id") or p.get("doi") or p.get("title")) not in classic_ids]

    # 清理内部字段
    for p in classic_papers + frontier_only:
        p.pop("_citation_velocity", None)
        p.pop("_citation_slope", None)

    # 标记轨道
    for p in classic_papers:
        p["track"] = "classic"
    for p in frontier_only:
        p["track"] = "frontier"

    return {
        "classic": classic_papers,
        "frontier": frontier_only,
        "stats": {
            "total_input": len(papers),
            "classic_count": len(classic_papers),
            "frontier_count": len(frontier_only),
            "classic_threshold": classic_threshold,
            "frontier_year_cutoff": frontier_cutoff_year,
            "frontier_slope_threshold": saved_slope_threshold,
        }
    }


def semantic_filter(papers, topic, relevance_threshold=0.15):
    """基于TF-IDF的语义权重过滤
    Args:
        papers: 论文列表(需含abstract字段)
        topic: 主题描述文本
        relevance_threshold: 相关性阈值(0-1)
    Returns:
        list: 过滤后的论文列表，每篇增加_relevance_score字段
    """
    if not HAS_SKLEARN:
        # 回退: 简单关键词匹配
        topic_words = set(topic.lower().split())
        filtered = []
        for p in papers:
            abstract = (p.get("abstract") or "").lower()
            title = (p.get("title") or "").lower()
            text = abstract + " " + title
            overlap = len(topic_words & set(text.split()))
            score = overlap / max(len(topic_words), 1)
            p["_relevance_score"] = round(score, 4)
            if score >= relevance_threshold:
                filtered.append(p)
        return filtered

    # 构建语料: 主题 + 所有论文摘要
    corpus = [topic]
    for p in papers:
        abstract = p.get("abstract") or p.get("title") or ""
        corpus.append(abstract)

    vectorizer = TfidfVectorizer(
        stop_words="english",
        max_features=5000,
        ngram_range=(1, 2),
        sublinear_tf=True,
    )
    try:
        tfidf_matrix = vectorizer.fit_transform(corpus)
    except ValueError:
        # 语料为空或无有效词汇
        for p in papers:
            p["_relevance_score"] = 0.0
        return papers

    # 主题向量为第一行
    topic_vec = tfidf_matrix[0:1]
    paper_vecs = tfidf_matrix[1:]

    similarities = cosine_similarity(topic_vec, paper_vecs)[0]

    filtered = []
    for i, p in enumerate(papers):
        score = float(similarities[i])
        p["_relevance_score"] = round(score, 4)
        if score >= relevance_threshold:
            filtered.append(p)

    # 按相关性降序排列
    filtered.sort(key=lambda x: x.get("_relevance_score", 0), reverse=True)
    return filtered


def main():
    parser = argparse.ArgumentParser(description="双轨筛选 + 语义权重过滤")
    parser.add_argument("--input", required=True, help="输入论文JSON文件路径")
    parser.add_argument("--topic", required=True, help="研究主题描述(用于语义匹配)")
    parser.add_argument("--classic_percentile", type=int, default=95, help="经典轨百分位(默认95=Top5%%)")
    parser.add_argument("--frontier_years", type=int, default=3, help="前沿轨年份窗口")
    parser.add_argument("--frontier_slope_percentile", type=int, default=70, help="前沿轨斜率百分位")
    parser.add_argument("--relevance_threshold", type=float, default=0.15, help="语义相关性阈值")
    parser.add_argument("--output", help="输出JSON文件路径(默认stdout)")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    papers = data if isinstance(data, list) else data.get("papers", [])

    # Step 1: 语义过滤
    semantically_filtered = semantic_filter(papers, args.topic, args.relevance_threshold)

    # Step 2: 双轨筛选
    dual_result = dual_track_filter(
        semantically_filtered,
        classic_percentile=args.classic_percentile,
        frontier_years=args.frontier_years,
        frontier_slope_percentile=args.frontier_slope_percentile,
    )

    # 合并结果并按轨道+相关性排序
    all_filtered = dual_result["classic"] + dual_result["frontier"]
    all_filtered.sort(key=lambda x: (
        0 if x.get("track") == "classic" else 1,
        -x.get("_relevance_score", 0)
    ))

    result = {
        "topic": args.topic,
        "total_input": len(papers),
        "semantic_filtered": len(semantically_filtered),
        "classic_count": dual_result["stats"]["classic_count"],
        "frontier_count": dual_result["stats"]["frontier_count"],
        "total_output": len(all_filtered),
        "stats": dual_result["stats"],
        "papers": all_filtered,
    }

    output_json = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
