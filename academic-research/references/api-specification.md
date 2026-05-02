# API规范与错误处理

## 目录

1. [Semantic Scholar API](#semantic-scholar-api)
2. [Unpaywall API](#unpaywall-api)
3. [Sci-Hub](#sci-hub)
4. [Zotero Web API](#zotero-web-api)
5. [CNKI集成说明](#cnki集成说明)
6. [通用错误处理策略](#通用错误处理策略)

---

## Semantic Scholar API

**基础URL**: `https://api.semanticscholar.org/graph/v1`

### 论文检索

| 端点 | 方法 | 说明 |
|------|------|------|
| `/paper/search` | GET | 关键词搜索 |
| `/paper/DOI:{doi}` | GET | DOI精确检索 |
| `/paper/{paper_id}` | GET | ID精确检索 |
| `/paper/batch` | POST | 批量检索(最多500篇) |

### 关键参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `query` | string | 搜索关键词 |
| `year` | string | 年份范围，如 `2020-2024` |
| `limit` | int | 返回数量(上限100) |
| `offset` | int | 分页偏移 |
| `fields` | string | 返回字段列表(逗号分隔) |

### 推荐fields

```
title,authors,year,abstract,citationCount,referenceCount,url,externalIds,openAccessPdf,fieldsOfStudy,publicationDate
```

### 速率限制

| 模式 | 限制 | 说明 |
|------|------|------|
| 无API Key | 100请求/5分钟 | 公开访问 |
| 有API Key | 1000请求/5分钟 | 申请: https://www.semanticscholar.org/product/api#api-key-form |

### 响应结构

```json
{
  "total": 1000,
  "offset": 0,
  "data": [
    {
      "paperId": "abc123",
      "title": "Paper Title",
      "authors": [{"authorId": "123", "name": "Author Name"}],
      "year": 2023,
      "abstract": "...",
      "citationCount": 42,
      "externalIds": {"DOI": "10.1234/...", "ArXiv": "2301.00001"},
      "openAccessPdf": {"url": "https://...", "status": "GREEN"}
    }
  ]
}
```

### 错误码

| HTTP状态码 | 含义 | 处理策略 |
|-----------|------|----------|
| 400 | 请求参数错误 | 检查参数格式 |
| 404 | 论文不存在 | 跳过该论文 |
| 429 | 速率限制 | 等待60秒后重试 |
| 500 | 服务器错误 | 指数退避重试(最多3次) |

---

## Unpaywall API

**基础URL**: `https://api.unpaywall.org/v2`

### Open Access查询

| 端点 | 方法 | 说明 |
|------|------|------|
| `/{doi}` | GET | 查询DOI的OA状态 |

### 参数

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `email` | string | 是 | 联系邮箱(必须真实有效) |

### 响应关键字段

```json
{
  "is_oa": true,
  "oa_status": "gold|green|hybrid|bronze|closed",
  "best_oa_location": {
    "url_for_pdf": "https://...",
    "url_for_landing_page": "https://...",
    "host_type": "publisher|repository"
  }
}
```

### OA状态说明

| 状态 | 说明 |
|------|------|
| gold | 出版商直接提供OA PDF |
| green | 仓储中可获取已接受版本 |
| hybrid | 混合OA期刊 |
| bronze | 免费获取但无明确许可 |
| closed | 无OA版本 |

### 速率限制

无硬性限制，但要求email真实有效。建议每秒不超过10个请求。

---

## Sci-Hub

**说明**: Sci-Hub无官方API，通过解析页面获取PDF链接。

### 镜像列表(按优先级)

| 镜像 | 域名 | 可用性 |
|------|------|--------|
| 1 | sci-hub.se | 不稳定 |
| 2 | sci-hub.st | 不稳定 |
| 3 | sci-hub.ru | 不稳定 |

### 使用方式

1. 访问 `https://{mirror}/{DOI}`
2. 解析HTML中的iframe/embed标签获取PDF URL
3. 下载PDF文件

### 注意事项

- Sci-Hub镜像可用性不稳定，脚本已实现多镜像自动切换
- 仅当Unpaywall未找到OA版本时才使用
- 部分PDF可能为扫描版本，文本提取质量较低
- 请遵守当地版权法规

---

## Zotero Web API

**基础URL**: `https://api.zotero.org`

### 认证

所有请求需在Header中携带: `Zotero-API-Key: {api_key}`

### 核心端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/users/{id}/items` | GET | 列出条目 |
| `/users/{id}/items` | POST | 创建条目 |
| `/users/{id}/items/{key}` | GET | 获取条目 |
| `/users/{id}/items/{key}` | PATCH | 更新条目 |
| `/users/{id}/items/{key}` | DELETE | 删除条目 |
| `/users/{id}/items/{key}/children` | GET | 获取子条目(笔记/附件) |

### 创建条目Payload格式

```json
[
  {
    "itemType": "journalArticle",
    "title": "Paper Title",
    "creators": [{"creatorType": "author", "firstName": "John", "lastName": "Doe"}],
    "abstractNote": "...",
    "date": "2023",
    "DOI": "10.1234/...",
    "tags": [{"tag": "machine learning"}]
  }
]
```

### 创建笔记Payload格式

```json
[
  {
    "itemType": "note",
    "parentItem": "{item_key}",
    "note": "<h1>深度内容解析</h1><p>...</p>",
    "tags": [{"tag": "深度解析"}]
  }
]
```

### 速率限制

- 每次请求返回Header `X-Backoff` 和 `Retry-After` 用于速率控制
- 默认限制: 未认证100请求/分钟，已认证400请求/分钟
- 写入操作建议间隔至少1秒

### API Key权限

创建API Key时需勾选:
- Allow library access - Read/Write
- Allow notes access - Read/Write
- Allow file access - Read/Write

---

## CNKI集成说明

CNKI(中国知网)无公开API，当前版本采用以下集成策略:

### 已支持

- 用户手动提供CNKI文献的DOI/标题，通过Semantic Scholar交叉检索
- 用户手动提供文献元数据JSON，直接进入筛选/解析流程

### 待扩展(需机构API权限)

- CNKI机构账号API接入
- 中文文献专用嵌入模型
- CNKI全文PDF下载

### 手动导入格式

```json
{
  "title": "论文标题",
  "authors": ["作者1", "作者2"],
  "year": 2023,
  "abstract": "摘要内容",
  "doi": "10.xxxx/xxxx",
  "citation_count": 50,
  "source": "cnki"
}
```

---

## 通用错误处理策略

### 重试策略

| 场景 | 策略 |
|------|------|
| 网络超时 | 指数退避重试(1s, 2s, 4s)，最多3次 |
| 429速率限制 | 等待Retry-After或默认60秒 |
| 500服务端错误 | 间隔5秒重试，最多2次 |
| 404不存在 | 跳过，记录日志 |
| PDF下载失败 | 标记为下载失败，尝试下一来源 |

### 降级策略

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1 | Unpaywall Open Access | 合法OA，优先使用 |
| 2 | ArXiv/Publisher OA | 直接下载 |
| 3 | Sci-Hub | 最后手段，可用性不稳定 |

### Session持久化保障

- 每步操作完成后立即写入Session文件
- Session文件采用追加式更新(merge)，不覆盖已有数据
- 异常中断后可从最新Session状态恢复
