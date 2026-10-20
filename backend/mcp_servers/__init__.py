"""本地 MCP 服务（FastMCP, stdio 传输）

这些脚本以 stdio 子进程方式被 hello-agents 的 MCPTool 拉起，无需任何网络端口/
假域名。工具数据统一来自 app.packs.customer_service.tools.mock_data（与进程内自定义工具同一份）。

启动/自测（backend 目录）：
    .\\.venv\\Scripts\\python.exe mcp_servers\\logistics_server.py
"""
