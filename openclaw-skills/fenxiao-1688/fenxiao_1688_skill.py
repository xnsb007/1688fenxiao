#!/usr/bin/env python3
"""1688分销选品技能 - 通过API与选品系统交互"""

import argparse
import os
import sys
import json
import urllib.request
import urllib.parse
import urllib.error

# 获取API地址
FENXIAO_API_URL = os.getenv("FENXIAO_API_URL", "https://fenxiao.1bgo.com")


def make_request(method, endpoint, data=None, params=None):
    """发送HTTP请求"""
    url = f"{FENXIAO_API_URL}{endpoint}"
    
    if params:
        query_string = urllib.parse.urlencode(params)
        url = f"{url}?{query_string}"
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    try:
        if method == "GET":
            req = urllib.request.Request(url, headers=headers, method="GET")
        else:
            req = urllib.request.Request(
                url, 
                data=json.dumps(data).encode('utf-8') if data else None,
                headers=headers, 
                method=method
            )
        
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return {"success": False, "error": f"HTTP Error: {e.code} - {e.reason}"}
    except urllib.error.URLError as e:
        return {"success": False, "error": f"URL Error: {e.reason}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def search_products(keyword, quantity=100, search_type="jxhy", price_min=None, price_max=None):
    """搜索商品"""
    data = {
        "keyword": keyword,
        "quantity": quantity,
        "search_type": search_type
    }
    if price_min is not None:
        data["price_min"] = price_min
    if price_max is not None:
        data["price_max"] = price_max
    
    result = make_request("POST", "/api/search", data=data)
    
    if result.get("success"):
        task_id = result.get("task_id")
        total = result.get("total", 0)
        selection_url = result.get("selection_url", f"{FENXIAO_API_URL}/selection/{task_id}")
        
        print(f"✅ 已为您从1688搜索到{total}件{keyword}商品！")
        print(f"\n请点击链接查看选品页面：")
        print(f"{selection_url}")
        print(f"\n您可以在页面中浏览商品详情，选择心仪的商品加入选品池。")
    else:
        print(f"❌ 搜索失败: {result.get('error', '未知错误')}")
    
    return result


def get_products(status="all", page=1, page_size=20, keyword=None, price_min=None, price_max=None):
    """获取产品库商品列表"""
    params = {
        "status": status,
        "page": page,
        "page_size": page_size
    }
    if keyword:
        params["keyword"] = keyword
    if price_min is not None:
        params["price_min"] = price_min
    if price_max is not None:
        params["price_max"] = price_max
    
    result = make_request("GET", "/api/products", params=params)
    
    if result.get("success"):
        products = result.get("products", [])
        total = result.get("total", 0)
        
        print(f"📦 产品库商品列表 (共{total}件):")
        print("-" * 60)
        for p in products:
            status_icon = "🟢" if p.get("is_listed") else "⚪"
            print(f"{status_icon} {p.get('title', '无标题')}")
            print(f"   ID: {p.get('offer_id')} | 价格: ¥{p.get('price', 'N/A')}")
            print()
    else:
        print(f"❌ 获取失败: {result.get('error', '未知错误')}")
    
    return result


def get_stats():
    """获取产品库统计"""
    result = make_request("GET", "/api/products/stats")
    
    if result.get("success"):
        stats = result.get("stats", {})
        print("📊 产品库统计：")
        print(f"- 总商品数：{stats.get('total', 0)}件")
        print(f"- 已选品：{stats.get('selected', 0)}件")
        print(f"- 已上架：{stats.get('listed', 0)}件")
    else:
        print(f"❌ 获取统计失败: {result.get('error', '未知错误')}")
    
    return result


def batch_list_products(offer_ids):
    """批量上架商品"""
    data = {"offer_ids": offer_ids}
    result = make_request("POST", "/api/products/batch/list", data=data)
    
    if result.get("success"):
        print(f"✅ 成功上架 {len(offer_ids)} 件商品")
    else:
        print(f"❌ 上架失败: {result.get('error', '未知错误')}")
    
    return result


def batch_unlist_products(offer_ids):
    """批量下架商品"""
    data = {"offer_ids": offer_ids}
    result = make_request("POST", "/api/products/batch/unlist", data=data)
    
    if result.get("success"):
        print(f"✅ 成功下架 {len(offer_ids)} 件商品")
    else:
        print(f"❌ 下架失败: {result.get('error', '未知错误')}")
    
    return result


def batch_delete_products(offer_ids):
    """批量删除商品"""
    data = {"offer_ids": offer_ids}
    result = make_request("POST", "/api/products/batch/delete", data=data)
    
    if result.get("success"):
        print(f"✅ 成功删除 {len(offer_ids)} 件商品")
    else:
        print(f"❌ 删除失败: {result.get('error', '未知错误')}")
    
    return result


def export_products(offer_ids=None, format="json"):
    """导出商品数据"""
    data = {"format": format}
    if offer_ids:
        data["offer_ids"] = offer_ids
    
    result = make_request("POST", "/api/products/export", data=data)
    
    if result.get("success"):
        export_url = result.get("export_url", "")
        print(f"✅ 导出成功！")
        if export_url:
            print(f"下载链接: {export_url}")
    else:
        print(f"❌ 导出失败: {result.get('error', '未知错误')}")
    
    return result


def main():
    parser = argparse.ArgumentParser(description="1688分销选品技能")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # search 命令
    search_parser = subparsers.add_parser("search", help="搜索商品")
    search_parser.add_argument("--keyword", required=True, help="搜索关键词")
    search_parser.add_argument("--quantity", type=int, default=100, help="搜索数量")
    search_parser.add_argument("--search-type", default="jxhy", help="搜索类型")
    search_parser.add_argument("--price-min", type=float, help="最低价格")
    search_parser.add_argument("--price-max", type=float, help="最高价格")
    
    # products 命令
    products_parser = subparsers.add_parser("products", help="获取产品库商品")
    products_parser.add_argument("--status", default="all", help="商品状态")
    products_parser.add_argument("--page", type=int, default=1, help="页码")
    products_parser.add_argument("--page-size", type=int, default=20, help="每页数量")
    products_parser.add_argument("--keyword", help="关键词筛选")
    
    # stats 命令
    subparsers.add_parser("stats", help="获取产品库统计")
    
    # batch-list 命令
    batch_list_parser = subparsers.add_parser("batch-list", help="批量上架")
    batch_list_parser.add_argument("--offer-ids", required=True, help="商品ID列表，逗号分隔")
    
    # batch-unlist 命令
    batch_unlist_parser = subparsers.add_parser("batch-unlist", help="批量下架")
    batch_unlist_parser.add_argument("--offer-ids", required=True, help="商品ID列表，逗号分隔")
    
    # batch-delete 命令
    batch_delete_parser = subparsers.add_parser("batch-delete", help="批量删除")
    batch_delete_parser.add_argument("--offer-ids", required=True, help="商品ID列表，逗号分隔")
    
    # export 命令
    export_parser = subparsers.add_parser("export", help="导出商品")
    export_parser.add_argument("--format", default="json", help="导出格式")
    export_parser.add_argument("--offer-ids", help="商品ID列表，逗号分隔")
    
    args = parser.parse_args()
    
    if args.command == "search":
        search_products(
            keyword=args.keyword,
            quantity=args.quantity,
            search_type=args.search_type,
            price_min=args.price_min,
            price_max=args.price_max
        )
    elif args.command == "products":
        get_products(
            status=args.status,
            page=args.page,
            page_size=args.page_size,
            keyword=args.keyword
        )
    elif args.command == "stats":
        get_stats()
    elif args.command == "batch-list":
        offer_ids = args.offer_ids.split(",")
        batch_list_products(offer_ids)
    elif args.command == "batch-unlist":
        offer_ids = args.offer_ids.split(",")
        batch_unlist_products(offer_ids)
    elif args.command == "batch-delete":
        offer_ids = args.offer_ids.split(",")
        batch_delete_products(offer_ids)
    elif args.command == "export":
        offer_ids = args.offer_ids.split(",") if args.offer_ids else None
        export_products(offer_ids=offer_ids, format=args.format)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
