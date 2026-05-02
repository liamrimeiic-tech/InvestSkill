---
name: academic-research
description: 提供学术文献检索-筛选-解析-入库全链路能力，支持双轨筛选（经典基石+前沿热点）与TF-IDF语义权重过滤；当用户需要进行学术调研、文献综述、前沿追踪或批量文献管理入库Zotero时使用
dependency:
  python:
    - pyzotero==1.11.1
    - pdfplumber==0.11.9
    - scikit-learn==1.8.0
    - numpy==2.4.3
---

# 学术调研Agent

## 任务目标
- 本 Skill 用于: 基于影响力和前沿性的智能学术文献调研，实现"检索-筛选-解析-入库"全链路闭环
- 能力包含: Semantic Scholar检索、双轨筛选(经典+前沿)、TF-IDF语义过滤、全文获取(Open Access+Sci-Hub)、PDF结构化提取、Zotero自动入库与笔记注入
- 触发条件: 用户提及"文献调研"、"文献综述"、"学术搜索"、"前沿追踪"、"论文筛选"、"Zotero入库"等需求

## 前置准备
- 依赖说明: pyzotero(Zotero API)、pdfplumber(PDF解析)、scikit-learn(TF-IDF语义评分)、numpy(数值计算)
- Zotero凭证: 需配置 `zotero_api` 凭证(API Key)，同时需用户提供 Library ID(在 https://www.zotero.org/settings/keys 页面顶部)
- Unpaywall邮箱: 默认使用 research@academic.edu，建议用户提供真实邮箱以获取更稳定的OA查询

## 操作步骤

### 阶段一: 创建Session与检索
1. 创建研究Session — 保存研究主题与状态
   - `python scripts/session_manager.py --operation create --topic "研究主题"`
   - 记录返回的 `session_id`，后续所有操作基于此Session
2. 检索英文文献(Semantic Scholar)
   - `python scripts/search_papers.py --query "检索词" --limit 50 --year_from 2020 --year_to 2024`
   - 多关键词批量检索: `python scripts/search_papers.py --queries '["keyword1","keyword2"]' --limit 20`
   - DOI精确检索: `python scripts/search_papers.py --doi "10.xxxx/xxxx"`
   - 引用追溯(Reference Snowballing): `python scripts/search_papers.py --snowball --paper_ids '["id1","id2"]' --min_co_cite 2 --limit 50`
     - 自动发现被多篇确认文献共引的高影响力参考文献
3. 保存检索结果到Session
   - `python scripts/session_manager.py --operation add_search_results --session_id <ID> --data '<JSON>'`

### 阶段二: 双轨筛选与语义过滤
4. 执行双轨筛选 + 语义过滤(一次性完成)
   - `python scripts/filter_papers.py --input <papers.json> --topic "研究主题描述" --classic_percentile 95 --frontier_years 3 --relevance_threshold 0.15`
   - 输出包含 `classic`(经典轨) 和 `frontier`(前沿轨) 两组论文
5. 保存筛选结果到Session
   - `python scripts/session_manager.py --operation update_state --session_id <ID> --state filtering --data '{"filtered_results": ...}'`

### 阶段三: 人机协同确认(关键节点)
6. 向用户展示预览列表 — **必须在执行高能耗操作前确认**
   - 格式: 按轨道分组，展示标题、年份、引用量、相关性分数、轨道标签
   - 示例输出格式:
     ```
     【经典轨】(领域基石研究)
     1. [2018] Attention Is All You Need | 引用: 85000 | 相关性: 0.45
     2. [2015] ResNet... | 引用: 120000 | 相关性: 0.38

     【前沿轨】(引用增长陡峭)
     3. [2024] Mamba: Linear-Time Sequence... | 引用: 890 | 相关性: 0.52
     4. [2023] LLaMA: Open... | 引用: 3200 | 相关性: 0.48
     ```
   - 询问用户: "是否确认对以上文献执行全文获取与深度解析？可指定序号范围或排除特定文献"
   - **失败重试指令**: 若有历史失败记录(`failed_papers`)，询问用户是否执行 `retry failed`
7. 根据用户确认更新Session
   - `python scripts/session_manager.py --operation add_confirmed_papers --session_id <ID> --paper_ids '["id1","id2"]'`
   - 若有失败重试: `python scripts/session_manager.py --operation get_failed --session_id <ID>` 获取需重试的DOI

### 阶段四: 全文获取与解析
8. 下载全文PDF(优先Open Access)
   - `python scripts/fetch_fulltext.py --doi "10.xxxx/xxxx"`
   - 直接URL下载: `python scripts/fetch_fulltext.py --url "https://..."`
   - PDF默认保存到 `./academic_pdfs/`（可通过环境变量 `ACADEMIC_PDF_DIR` 覆盖）
   - 下载完成后标记: `python scripts/session_manager.py --operation mark_downloaded --session_id <ID> --paper_id <PID> --download_status success --download_path <path> --download_source unpaywall`
   - 下载失败标记: `python scripts/session_manager.py --operation mark_downloaded --session_id <ID> --paper_id <PID> --download_status failed --download_error "镜像不可用"`
9. 提取PDF结构化内容(字体感知增强版)
   - `python scripts/parse_paper.py --pdf_path <path> --extract_sections true --extract_metadata true --font_aware`
   - 支持: 正则章节检测(中英双语) + 字体大小启发式检测(双栏排版)
10. **智能体深度分析**(非脚本): 基于提取的全文/章节，分析并结构化输出:
    - 研究方法: 识别实验设计、数据集、评估指标
    - 样本规模: 提取实验数据量级
    - 核心结论: 总结关键发现与贡献
    - 局限性: 识别作者自述与隐含局限
    - 对比分析: 生成"本文 vs 其他已解析文献的方法论差异"对比段落

### 阶段四-B: 引用追溯(可选，推荐在首次确认文献解析完成后执行)
11. 获取确认文献的参考文献共引网络
    - `python scripts/search_papers.py --snowball --paper_ids '["S12","S3"]' --min_co_cite 2 --limit 50`
    - 返回被多篇确认文献共引的领域基石文献（独立于原始检索结果）
12. 将追溯结果追加进筛选池
    - `python scripts/session_manager.py --operation add_snowballing --session_id <ID> --data '<snowball_result>'`
    - 询问用户是否对新增文献执行新一轮确认-解析-入库

### 阶段五: Zotero入库与同步
13. 生成结构化Extra标签(用于Zotero搜索框直接筛选)
    - `python scripts/zotero_sync.py --operation build_extra_tags --library_id <ID> --parsed_content '<JSON>' --paper_data '<JSON>'`
    - 输出格式: `method:diff-in-diff; sample:5000; track:classic; limitation:endogeneity`
14. 生成笔记内容与重命名格式(含轨道标识)
    - `python scripts/zotero_sync.py --operation build_note --library_id <ID> --parsed_content '<JSON>' --paper_data '<JSON>'`
    - 文件名格式: `[Classic] 2017-Gorton-Securitization Credit Arbitrage.pdf` / `[Frontier] 2024-Smith-...pdf`
15. 创建Zotero条目
    - `python scripts/zotero_sync.py --operation create_item --library_id <ID> --item_data '<JSON>'`
    - 批量创建: `python scripts/zotero_sync.py --operation batch_create --library_id <ID> --items_file <path>`
16. 注入深度内容笔记
    - `python scripts/zotero_sync.py --operation add_note --library_id <ID> --item_key <KEY> --note_content '<HTML>'`
17. 生成并注入跨文献对比笔记(所有已解析文献解析完成后)
    - `python scripts/zotero_sync.py --operation build_comparison --library_id <ID> --paper_data '<JSON>' --parsed_content '<other_papers_JSON>'`
18. 更新Session状态
    - `python scripts/session_manager.py --operation update_state --session_id <ID> --state completed`
    - `python scripts/session_manager.py --operation save_comparison --session_id <ID> --paper_id <PID> --comparison_text '<对比内容>'`

### 可选分支
- 当某篇论文无法获取全文: 跳过深度解析，仅入库元数据并在笔记中标注"全文待获取"，Session中记录到 `failed_papers`
- 当用户仅需检索和筛选(不入库Zotero): 执行至阶段三后结束，将结果以结构化表格输出
- 当需要增量检索: 使用Session恢复，追加新的检索结果后重新筛选
- 当下载失败需重试: 读取 `failed_papers` 列表，尝试切换Sci-Hub镜像重试
- 当引用追溯发现新文献: 追加进Session后，从阶段三重新开始确认流程

## 使用示例

### 示例1: 完整文献调研流程
- 场景/输入: 用户需要调研"大语言模型的高效推理"领域
- 预期产出: 双轨筛选后的文献列表 + 深度内容解析 + Zotero自动入库
- 关键要点:
  1. 创建Session保持状态
  2. 使用多关键词检索覆盖面更广
  3. 确认节点不可跳过
  4. 入库前需用户提供Zotero Library ID

### 示例2: 快速前沿追踪
- 场景/输入: 用户只需追踪某领域近2年最新进展
- 预期产出: 前沿轨文献列表(含引用斜率)，不入库Zotero
- 关键要点:
  1. 设置 `--frontier_years 2 --frontier_slope_percentile 60` 扩大前沿捕获面
  2. 可跳过阶段五(不入库)
  3. 不需要Zotero凭证

### 示例3: 中文文献手动导入与解析
- 场景/输入: 用户提供CNKI论文元数据JSON，需解析入库
- 预期产出: PDF解析 + 结构化笔记 + Zotero入库
- 关键要点:
  1. 手动导入格式见 references/api-specification.md 的"CNKI集成说明"
  2. 跳过阶段一(检索)，直接进入筛选/解析
  3. 中文PDF解析质量可能较低，智能体需辅助补充

## 资源索引
- 脚本: [scripts/search_papers.py](scripts/search_papers.py) — Semantic Scholar检索，支持关键词/DOI/批量模式、引用追溯(snowball)、参考文献/引用列表获取
- 脚本: [scripts/filter_papers.py](scripts/filter_papers.py) — 双轨筛选+TF-IDF语义过滤，输入论文JSON与主题描述
- 脚本: [scripts/fetch_fulltext.py](scripts/fetch_fulltext.py) — 全文PDF获取，Unpaywall优先+Sci-Hub回退，PDF默认存至 `./academic_pdfs/`（环境变量 `ACADEMIC_PDF_DIR` 可覆盖）
- 脚本: [scripts/parse_paper.py](scripts/parse_paper.py) — PDF结构化提取，字体感知章节检测(双栏排版)+中英双语正则检测，含样本量/关键词自动提取
- 脚本: [scripts/zotero_sync.py](scripts/zotero_sync.py) — Zotero操作，含笔记生成(build_note)、Extra字段标签(build_extra_tags)、跨文献对比(build_comparison)、重命名(含[Classic]/[Frontier]轨道前缀)
- 脚本: [scripts/session_manager.py](scripts/session_manager.py) — Session状态持久化，含失败重试队列(failed_papers)、引用追溯结果(snowballing)、跨文献对比笔记(cross_paper_comparisons)
- 参考: [references/api-specification.md](references/api-specification.md) — 各API端点、参数、速率限制与错误处理策略
- 参考: [references/filtering-algorithm.md](references/filtering-algorithm.md) — 双轨算法原理、引用斜率公式、TF-IDF评分与参数调优

## 注意事项
- 阶段三(人机协同确认)为强制节点，禁止在用户未确认时执行全文下载或Zotero写入
- 优先检索英文文献(Semantic Scholar)，中文文献需手动提供元数据
- 所有解析结果、对比结论与交互界面使用中文，保持专业中立高效风格
- 语义相关性阈值默认0.15，可根据主题宽窄度调整，详见 references/filtering-algorithm.md
- Sci-Hub为全文获取的最后手段，可用性不稳定，优先使用Open Access
- Zotero API Key需配置权限: Library Read/Write + Notes Read/Write + Files Read/Write
- Session文件存储于 `~/.academic_research_sessions/`，异常中断后可恢复；含 `failed_papers` 队列记录下载失败项
- PDF下载目录默认为 `./academic_pdfs/`（用户可通过环境变量 `ACADEMIC_PDF_DIR` 自定义路径），解析结果通过 `--output` 参数指定路径
- 智能体负责深度内容分析(研究方法/样本规模/核心结论/局限性/跨文献对比)，脚本仅提取原始文本
- 字体感知章节检测需 pdfplumber 支持字体信息提取，非所有PDF均有效，建议配合正则检测使用
- Zotero Extra字段格式: `method:xxx; sample:N=xxxx; track:classic; limitation:xxx`，可在Zotero搜索框直接检索
- 引用追溯功能建议在首批确认文献解析完成后执行，发现的新文献需经用户确认后进入新一轮解析流程
- PDF文件自动重命名格式: `[Classic] 2023-Smith-Title.pdf` / `[Frontier] 2024-Author-Title.pdf`，一眼区分文献权重
