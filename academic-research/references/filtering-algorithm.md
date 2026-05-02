# 双轨筛选算法与语义评分

## 目录

1. [算法概览](#算法概览)
2. [经典轨: 引用量Top 5%筛选](#经典轨)
3. [前沿轨: 近期+引用斜率筛选](#前沿轨)
4. [TF-IDF语义权重过滤](#tf-idf语义权重过滤)
5. [综合评分与排序](#综合评分与排序)
6. [参数调优指南](#参数调优指南)

---

## 算法概览

双轨筛选法确保同时捕获"领域基石研究"和"新兴前沿研究"，避免单一时间维度或引用维度带来的偏差。

```
输入论文集合
    │
    ├──────────────────────────────┐
    ▼                              ▼
语义权重过滤(TF-IDF)         ┌─────┴─────┐
(摘要相关性 >= 阈值)          │           │
    │                        ▼           ▼
    │                   经典轨       前沿轨
    │              (引用Top5%)   (近期+斜率)
    │                   │           │
    └───────────────────┴─────┬─────┘
                              ▼
                        合并去重排序
                              │
                              ▼
                        输出预览列表
```

**核心约束**: 禁止仅按时间排序。两条轨道的结果必须独立存在，最终合并时保留轨道标签。

---

## 经典轨

### 目标
锁定领域内引用量最高的基石研究(高引用 = 高影响力 = 领域核心文献)。

### 筛选条件

```
citation_count >= percentile(citation_counts, 95)
```

即: 在所有输入论文中，引用量排名前5%的论文进入经典轨。

### 百分位计算

- 论文数 >= 5: 使用 `numpy.percentile` 计算精确百分位
- 论文数 < 5: 取引用量排序后的最高值作为阈值

### 注意事项

- 经典轨不限制发表年份
- 引用量为累计值，旧论文天然有优势(符合"基石研究"定位)
- 经典轨论文可能同时满足前沿轨条件(去重时保留经典标签)

---

## 前沿轨

### 目标
锁定近期发表(2-3年内)且引用增长迅速(斜率陡峭)的新兴研究。

### 筛选条件

```
条件1: publication_year >= (current_year - frontier_years)
条件2: citation_slope >= percentile(slopes, frontier_slope_percentile)
```

两个条件**同时满足**才进入前沿轨。

### 引用斜率计算

采用启发式加速模型，因Semantic Scholar API不提供历史引用时序数据:

```
citation_velocity = citation_count / max(years_since_publication, 1)
recency_boost = exp(-0.3 * (years_since_publication - 1))  # if <= 5 years
citation_slope = citation_velocity * (1 + recency_boost)
```

**原理说明**:

- `citation_velocity`: 平均每年引用量，衡量绝对速度
- `recency_boost`: 近期加速因子，论文越新加速越明显
  - 1年内: boost ≈ 1.74 (陡峭增长)
  - 2年内: boost ≈ 1.29
  - 3年内: boost ≈ 0.96
  - 5年内: boost ≈ 0.53
  - >5年: boost = 0.1 (衰减为常数)

**斜率百分位阈值**: 默认70%，即近3年论文中斜率排名前30%进入前沿轨。

### 前沿轨参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `frontier_years` | 3 | 前沿年份窗口 |
| `frontier_slope_percentile` | 70 | 斜率百分位阈值 |

---

## TF-IDF语义权重过滤

### 目标
确保摘要内容与主题高度相关，而非仅标题命中。过滤掉标题匹配但内容偏离的论文。

### 方法

使用TF-IDF + 余弦相似度计算论文摘要与研究主题的语义相关性:

1. **语料构建**: `[主题描述] + [论文1摘要, 论文2摘要, ...]`
2. **向量化**: TF-IDF (unigram + bigram, sublinear TF, 最多5000特征)
3. **相似度计算**: 主题向量与每个摘要向量的余弦相似度
4. **过滤**: 相关性分数 < 阈值的论文被移除

### 关键参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `relevance_threshold` | 0.15 | 相关性阈值(0-1) |
| `ngram_range` | (1, 2) | unigram + bigram |
| `max_features` | 5000 | 最大特征数 |
| `sublinear_tf` | True | 使用对数TF(减少高频词影响) |

### 阈值选择指南

| 阈值范围 | 效果 | 适用场景 |
|----------|------|----------|
| 0.05-0.10 | 宽松，保留更多论文 | 探索性调研 |
| 0.10-0.20 | 平衡(推荐) | 常规文献综述 |
| 0.20-0.35 | 严格，只保留高度相关 | 精确主题检索 |
| >0.35 | 极严格 | 几乎只保留完全匹配 |

### 降级策略

当 `scikit-learn` 不可用时，降级为关键词重叠率:
```
overlap_rate = |topic_words ∩ (abstract_words ∪ title_words)| / |topic_words|
```
此方法精度较低，建议仅在无法安装scikit-learn时使用。

---

## 综合评分与排序

### 排序规则

最终输出按以下优先级排序:

1. **轨道优先**: 经典轨论文排在前沿轨之前
2. **轨道内按相关性降序**: 语义相关性分数高的论文排在前面

```
sort_key = (track_priority, -relevance_score)
# track_priority: classic=0, frontier=1
```

### 输出结构

```json
{
  "topic": "研究主题",
  "total_input": 100,
  "semantic_filtered": 65,
  "classic_count": 5,
  "frontier_count": 8,
  "total_output": 13,
  "stats": { ... },
  "papers": [
    {
      "paper_id": "...",
      "title": "...",
      "track": "classic|frontier",
      "_relevance_score": 0.42,
      "citation_count": 1500,
      "year": 2020,
      ...
    }
  ]
}
```

---

## 参数调优指南

### 经典文献过多/过少

| 现象 | 调整 |
|------|------|
| 经典文献太少(0-1篇) | 降低 `classic_percentile` (如90→85) |
| 经典文献太多(>10篇) | 提高 `classic_percentile` (如95→98) |
| 经典文献质量不高 | 提高语义相关性阈值 |

### 前沿文献过多/过少

| 现象 | 调整 |
|------|------|
| 前沿文献太少 | 降低 `frontier_slope_percentile` (如70→50) |
| 前沿文献太多 | 提高 `frontier_slope_percentile` (如70→85) |
| 前沿文献不够"前沿" | 减小 `frontier_years` (如3→2) |

### 语义过滤偏差

| 现象 | 调整 |
|------|------|
| 相关论文被误过滤 | 降低 `relevance_threshold` (如0.15→0.10) |
| 不相关论文混入 | 提高 `relevance_threshold` (如0.15→0.25) |
| 主题描述太短/太长 | 优化主题描述(建议10-50词，包含关键术语) |

### 多关键词策略

使用 `search_papers.py --queries` 进行多关键词检索:
- 同义词扩展: `["transformer attention", "self-attention mechanism"]`
- 领域上下位词: `["large language model", "foundation model", "pretrained language model"]`
- 中英文双语: `["knowledge distillation", "知识蒸馏"]`
