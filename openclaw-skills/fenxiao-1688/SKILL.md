---
name: fenxiao-1688
description: 1688分销选品系统 - 从1688平台搜索商品、管理产品库、批量上架下架、导出数据。当用户需要从1688找商品、搜索1688商品、查看分销产品库、上架下架商品时使用此技能。
triggers:
  - "1688"
  - "从1688"
  - "找1688"
  - "搜索1688"
  - "1688搜索"
  - "1688商品"
  - "分销"
  - "选品"
  - "产品库"
  - "上架"
  - "下架"
  - "导出商品"
  - "给我找"
  - "帮我找"
  - "找商品"
  - "找产品"
metadata: {"clawdbot":{"emoji":"🛒","requires":{"bins":["python3"]},"config":{"env":{"FENXIAO_API_URL":{"description":"API服务地址","default":"https://fenxiao.1bgo.com","required":true}}}}}
allowed-tools: Bash(python3), Bash(curl)
---

# 1688分销选品助手

通过自然语言与1688分销选品系统交互，实现商品搜索、产品库管理、批量操作等功能。

## 重要提示

**当用户提到1688、分销、选品、产品库、上架、下架等关键词时，必须使用此技能，而不是searxng或其他搜索技能。**

## 工具定义

### search_products

从1688平台搜索商品并创建选品任务。

**用途**: 当用户要求从1688搜索、查找、获取商品时使用此工具。**优先使用此工具处理1688相关搜索。**

**参数**:
- `keyword` (string, 必需): 搜索关键词，如"羽绒服"、"书包"等
- `quantity` (integer, 可选): 需要搜索的商品数量，默认100
- `search_type` (string, 可选): 搜索类型，"jxhy"表示精选货源，"keywords"表示关键词搜索，默认"jxhy"
- `price_min` (number, 可选): 最低价格筛选
- `price_max` (number, 可选): 最高价格筛选

**执行命令**:
```bash
python3 /root/.openclaw/workspace/skills/fenxiao-1688/fenxiao_1688_skill.py search --keyword "书包" --quantity 20
```

**API调用**:
```
POST ${FENXIAO_API_URL}/api/search
Content-Type: application/json

{
  "keyword": "书包",
  "quantity": 20
}
```

**返回示例**:
```json
{
  "success": true,
  "task_id": "abc12345",
  "total": 20,
  "selection_url": "https://fenxiao.1bgo.com/selection/abc12345"
}
```

**响应格式**:
如果成功，返回选品页面链接，用户可以点击链接查看商品详情。

---

### get_products

获取产品库中的商品列表。

**用途**: 当用户要求查看产品库、商品列表时使用此工具。

**参数**:
- `status` (string, 可选): 商品状态，"all"(全部)、"selected"(已选品)、"listed"(已上架)，默认"all"
- `page` (integer, 可选): 页码，默认1
- `page_size` (integer, 可选): 每页数量，默认20
- `keyword` (string, 可选): 标题关键词筛选
- `price_min` (number, 可选): 最低价格筛选
- `price_max` (number, 可选): 最高价格筛选

**执行命令**:
```bash
python3 /root/.openclaw/workspace/skills/fenxiao-1688/fenxiao_1688_skill.py products --status all --page 1 --page_size 20
```

**API调用**:
```
GET ${FENXIAO_API_URL}/api/products?status=all&page=1&page_size=20
```

---

### get_stats

获取产品库统计数据。

**用途**: 当用户要求查看统计、概览时使用此工具。

**执行命令**:
```bash
python3 /root/.openclaw/workspace/skills/fenxiao-1688/fenxiao_1688_skill.py stats
```

**API调用**:
```
GET ${FENXIAO_API_URL}/api/products/stats
```

**返回示例**:
```json
{
  "success": true,
  "stats": {
    "total": 150,
    "selected": 100,
    "listed": 50
  }
}
```

---

### batch_list_products

批量上架商品。

**用途**: 当用户要求上架商品时使用此工具。

**参数**:
- `offer_ids` (array of strings, 必需): 要上架的商品ID列表

**执行命令**:
```bash
python3 /root/.openclaw/workspace/skills/fenxiao-1688/fenxiao_1688_skill.py batch-list --offer-ids "id1,id2,id3"
```

**API调用**:
```
POST ${FENXIAO_API_URL}/api/products/batch/list
Content-Type: application/json

{
  "offer_ids": ["id1", "id2", "id3"]
}
```

---

### batch_unlist_products

批量下架商品。

**用途**: 当用户要求下架商品时使用此工具。

**参数**:
- `offer_ids` (array of strings, 必需): 要下架的商品ID列表

**执行命令**:
```bash
python3 /root/.openclaw/workspace/skills/fenxiao-1688/fenxiao_1688_skill.py batch-unlist --offer-ids "id1,id2,id3"
```

**API调用**:
```
POST ${FENXIAO_API_URL}/api/products/batch/unlist
Content-Type: application/json

{
  "offer_ids": ["id1", "id2", "id3"]
}
```

---

### batch_delete_products

批量删除商品。

**用途**: 当用户要求删除商品时使用此工具。

**参数**:
- `offer_ids` (array of strings, 必需): 要删除的商品ID列表

**执行命令**:
```bash
python3 /root/.openclaw/workspace/skills/fenxiao-1688/fenxiao_1688_skill.py batch-delete --offer-ids "id1,id2,id3"
```

**API调用**:
```
POST ${FENXIAO_API_URL}/api/products/batch/delete
Content-Type: application/json

{
  "offer_ids": ["id1", "id2", "id3"]
}
```

---

### export_products

导出商品数据。

**用途**: 当用户要求导出数据时使用此工具。

**参数**:
- `offer_ids` (array of strings, 可选): 要导出的商品ID列表，不传则导出全部
- `format` (string, 可选): 导出格式，"json"、"csv"、"excel"，默认"json"

**执行命令**:
```bash
python3 /root/.openclaw/workspace/skills/fenxiao-1688/fenxiao_1688_skill.py export --format excel --offer-ids "id1,id2"
```

**API调用**:
```
POST ${FENXIAO_API_URL}/api/products/export
Content-Type: application/json

{
  "offer_ids": ["id1", "id2"],
  "format": "excel"
}
```

---

## 使用示例

### 示例1: 搜索商品

用户输入:
```
从1688给我找20件书包
```

AI解析意图后执行:
```bash
python3 /root/.openclaw/workspace/skills/fenxiao-1688/fenxiao_1688_skill.py search --keyword "书包" --quantity 20
```

AI回复:
```
已为您从1688搜索到20件书包商品！

请点击链接查看选品页面：
https://fenxiao.1bgo.com/selection/abc12345

您可以在页面中浏览商品详情，选择心仪的商品加入选品池。
```

### 示例2: 查看统计

用户输入:
```
查看产品库统计
```

AI执行:
```bash
python3 /root/.openclaw/workspace/skills/fenxiao-1688/fenxiao_1688_skill.py stats
```

AI回复:
```
📊 产品库统计：
- 总商品数：150件
- 已选品：100件
- 已上架：50件
```

### 示例3: 批量上架

用户输入:
```
把所有待上架的商品都上架
```

AI先执行 get_products 获取待上架商品，再执行 batch_list_products 执行上架。

---

## 配置要求

### 环境变量

| 变量名 | 说明 | 示例值 |
|--------|------|--------|
| FENXIAO_API_URL | API服务地址 | https://fenxiao.1bgo.com |

### OpenClaw配置

在 `~/.openclaw/openclaw.json` 中配置：

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.openclaw/workspace"
    }
  },
  "skills": {
    "entries": {
      "fenxiao-1688": {
        "enabled": true,
        "env": {
          "FENXIAO_API_URL": "https://fenxiao.1bgo.com"
        }
      }
    },
    "load": {
      "extraDirs": ["/opt/fenxiao/openclaw-skills"]
    }
  },
  "channels": {
    "dingtalk": {
      "enabled": true,
      "requireMention": true
    }
  }
}
```

## 页面链接

| 页面 | URL格式 |
|------|---------|
| 选品页面 | {FENXIAO_API_URL}/selection/{task_id} |
| 产品库 | {FENXIAO_API_URL}/products |
