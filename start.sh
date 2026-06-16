#!/bin/bash

# 启动脚本 - 用于阿里云Linux服务器
# 同时启动Flask Web服务和钉钉Stream服务

cd "$(dirname "$0")"

mkdir -p logs
TODAY=$(date +%Y%m%d)
WEB_LOG="logs/web-${TODAY}.log"
DINGTALK_LOG="logs/dingtalk-${TODAY}.log"

echo "========================================"
echo "启动分销选品系统"
echo "========================================"
echo ""

# 检查虚拟环境
if [ -d "venv" ]; then
    echo "✓ 激活虚拟环境"
    source venv/bin/activate
elif [ -d ".venv" ]; then
    echo "✓ 激活虚拟环境"
    source .venv/bin/activate
else
    echo "⚠ 未找到虚拟环境，使用系统Python"
fi

# 检查配置文件
if [ -f "config/production.env" ]; then
    echo "✓ 找到配置文件: config/production.env"
elif [ -f "/opt/fenxiao/config/production.env" ]; then
    echo "✓ 找到配置文件: /opt/fenxiao/config/production.env"
else
    echo "❌ 错误: 未找到配置文件"
    echo "请创建 config/production.env 文件"
    exit 1
fi

echo ""
echo "启动Flask Web服务..."
nohup python run.py >> "$WEB_LOG" 2>&1 &
WEB_PID=$!
echo "✓ Web服务已启动 (PID: $WEB_PID)"

echo ""
echo "启动钉钉Stream服务..."
nohup python run_dingtalk_stream.py >> "$DINGTALK_LOG" 2>&1 &
DINGTALK_PID=$!
echo "✓ 钉钉Stream服务已启动 (PID: $DINGTALK_PID)"

echo ""
echo "========================================"
echo "服务启动完成"
echo "========================================"
echo ""
echo "Web服务日志: tail -f $WEB_LOG"
echo "钉钉服务日志: tail -f $DINGTALK_LOG"
echo ""
echo "停止服务:"
echo "  kill $WEB_PID"
echo "  kill $DINGTALK_PID"
echo ""

# 保存PID到文件
echo "$WEB_PID" > logs/web.pid
echo "$DINGTALK_PID" > logs/dingtalk.pid
