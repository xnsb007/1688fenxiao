#!/bin/bash

# 停止脚本 - 用于停止所有服务

cd "$(dirname "$0")"

echo "========================================"
echo "停止分销选品系统"
echo "========================================"
echo ""

# 停止Web服务
if [ -f "logs/web.pid" ]; then
    WEB_PID=$(cat logs/web.pid)
    if kill -0 $WEB_PID 2>/dev/null; then
        echo "停止Web服务 (PID: $WEB_PID)..."
        kill $WEB_PID
        echo "✓ Web服务已停止"
    else
        echo "⚠ Web服务未运行"
    fi
    rm -f logs/web.pid
else
    echo "⚠ 未找到Web服务PID文件"
fi

# 停止钉钉服务
if [ -f "logs/dingtalk.pid" ]; then
    DINGTALK_PID=$(cat logs/dingtalk.pid)
    if kill -0 $DINGTALK_PID 2>/dev/null; then
        echo "停止钉钉服务 (PID: $DINGTALK_PID)..."
        kill $DINGTALK_PID
        echo "✓ 钉钉服务已停止"
    else
        echo "⚠ 钉钉服务未运行"
    fi
    rm -f logs/dingtalk.pid
else
    echo "⚠ 未找到钉钉服务PID文件"
fi

echo ""
echo "========================================"
echo "服务已停止"
echo "========================================"
