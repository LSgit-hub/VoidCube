"""
OPS 注册 - 系统运维工具（精简版）
"""

import json
import psutil
import platform
import subprocess
import os
from typing import Dict, Any, List

from tools.registry import registry


def system_info_tool(args=None):
    """获取系统信息"""
    try:
        info = {
            "success": True,
            "os": platform.system(),
            "os_version": platform.version(),
            "os_release": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python_version": platform.python_version(),
            "hostname": platform.node(),
        }
        return json.dumps(info, ensure_ascii=False)
    except (RuntimeError, OSError) as e:
        return json.dumps({"success": False, "error": str(e)})


def cpu_stats_tool(args=None):
    """获取CPU统计"""
    try:
        cpu_count = psutil.cpu_count(logical=True)
        cpu_count_physical = psutil.cpu_count(logical=False)
        cpu_percent = psutil.cpu_percent(percpu=True)
        cpu_freq = psutil.cpu_freq()
        
        info = {
            "success": True,
            "cpu_count_logical": cpu_count,
            "cpu_count_physical": cpu_count_physical,
            "cpu_percent": cpu_percent,
            "cpu_freq_current": cpu_freq.current if cpu_freq else None,
            "cpu_freq_min": cpu_freq.min if cpu_freq else None,
            "cpu_freq_max": cpu_freq.max if cpu_freq else None,
        }
        return json.dumps(info, ensure_ascii=False)
    except (RuntimeError, OSError, psutil.Error) as e:
        return json.dumps({"success": False, "error": str(e)})


def memory_stats_tool(args=None):
    """获取内存统计"""
    try:
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        
        info = {
            "success": True,
            "total": mem.total,
            "available": mem.available,
            "used": mem.used,
            "free": mem.free,
            "percent": mem.percent,
            "swap_total": swap.total,
            "swap_used": swap.used,
            "swap_free": swap.free,
            "swap_percent": swap.percent,
        }
        return json.dumps(info, ensure_ascii=False)
    except (RuntimeError, OSError, psutil.Error) as e:
        return json.dumps({"success": False, "error": str(e)})


def disk_usage_tool(args=None):
    """获取磁盘使用情况"""
    try:
        partitions = psutil.disk_partitions()
        usage_list = []
        
        for part in partitions:
            try:
                usage = psutil.disk_usage(part.mountpoint)
                usage_list.append({
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "percent": usage.percent,
                })
            except (OSError, psutil.Error):
                pass
        
        return json.dumps({"success": True, "disks": usage_list}, ensure_ascii=False)
    except (RuntimeError, OSError, psutil.Error) as e:
        return json.dumps({"success": False, "error": str(e)})


def top_processes_tool(args=None):
    """获取进程列表"""
    try:
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'username']):
            try:
                processes.append({
                    "pid": proc.info['pid'],
                    "name": proc.info['name'],
                    "cpu_percent": proc.info['cpu_percent'],
                    "memory_percent": proc.info['memory_percent'],
                    "username": proc.info['username'],
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        
        processes.sort(key=lambda x: x['cpu_percent'] if x['cpu_percent'] is not None else 0, reverse=True)
        top_10 = processes[:10]
        
        return json.dumps({"success": True, "processes": top_10}, ensure_ascii=False)
    except (RuntimeError, OSError, psutil.Error) as e:
        return json.dumps({"success": False, "error": str(e)})


def ping_tool(args):
    """Ping测试"""
    try:
        host = args.get("host", "google.com")
        count = args.get("count", 4)
        
        param = '-n' if os.name == 'nt' else '-c'
        result = subprocess.run(
            ['ping', param, str(count), host],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        return json.dumps({
            "success": True,
            "host": host,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }, ensure_ascii=False)
    except (subprocess.CalledProcessError, TimeoutError, OSError) as e:
        return json.dumps({"success": False, "error": str(e)})


def check_port_tool(args):
    """检查端口是否开放"""
    import socket
    try:
        host = args.get("host", "localhost")
        port = args.get("port", 80)
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((host, port))
        sock.close()
        
        return json.dumps({
            "success": True,
            "host": host,
            "port": port,
            "open": result == 0,
        }, ensure_ascii=False)
    except (socket.gaierror, OSError) as e:
        return json.dumps({"success": False, "error": str(e)})


def dns_lookup_tool(args):
    """DNS查询"""
    import socket
    try:
        domain = args.get("domain", "example.com")
        record_type = args.get("record_type", "A")
        
        result = socket.getaddrinfo(domain, None)
        addresses = list(set(ai[4][0] for ai in result))
        
        return json.dumps({
            "success": True,
            "domain": domain,
            "addresses": addresses
        }, ensure_ascii=False)
    except (socket.gaierror, OSError) as e:
        return json.dumps({"success": False, "error": str(e)})


def curl_check_tool(args):
    """HTTP请求测试"""
    try:
        url = args.get("url", "https://example.com")
        
        import urllib.request
        req = urllib.request.Request(url, method='GET')
        
        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read().decode('utf-8', errors='ignore')
            
            return json.dumps({
                "success": True,
                "url": url,
                "status_code": response.status,
                "headers": dict(response.headers),
                "content_length": len(content),
            }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


def read_log_tool(args):
    """读取日志文件"""
    try:
        file_path = args.get("file_path", "")
        lines = args.get("lines", 100)
        
        if file_path and os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.readlines()
                last_lines = content[-lines:]
            
            return json.dumps({
                "success": True,
                "file_path": file_path,
                "lines_read": len(last_lines),
                "content": "".join(last_lines)
            }, ensure_ascii=False)
        
        return json.dumps({"success": False, "error": "File not found"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


def log_errors_tool(args):
    """读取日志中的错误"""
    try:
        file_path = args.get("file_path", "")
        
        if file_path and os.path.exists(file_path):
            import re
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.readlines()
                error_lines = [line for line in content if re.search(r'error|ERROR|Error', line)]
            
            return json.dumps({
                "success": True,
                "file_path": file_path,
                "error_count": len(error_lines),
                "error_lines": "".join(error_lines[-50:])
            }, ensure_ascii=False)
        
        return json.dumps({"success": False, "error": "File not found"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


def analyze_log_tool(args):
    """分析日志文件"""
    try:
        file_path = args.get("file_path", "")
        
        if file_path and os.path.exists(file_path):
            import re
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            total_lines = len(content.splitlines())
            error_count = len(re.findall(r'error|ERROR|Error', content))
            warning_count = len(re.findall(r'warning|WARNING|Warning', content))
            
            return json.dumps({
                "success": True,
                "file_path": file_path,
                "total_lines": total_lines,
                "error_count": error_count,
                "warning_count": warning_count,
            }, ensure_ascii=False)
        
        return json.dumps({"success": False, "error": "File not found"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


def register_ops_tools() -> List[str]:
    """注册运维工具（精简版）"""
    registered_tools = []
    
    system_info_schema = {
        "description": "获取系统信息",
        "parameters": {}
    }
    registry.register(
        name="system_info",
        toolset="system",
        schema=system_info_schema,
        handler=system_info_tool,
    )
    registered_tools.append("system_info")
    
    cpu_stats_schema = {
        "description": "获取CPU统计信息",
        "parameters": {}
    }
    registry.register(
        name="cpu_stats",
        toolset="system",
        schema=cpu_stats_schema,
        handler=cpu_stats_tool,
    )
    registered_tools.append("cpu_stats")
    
    memory_stats_schema = {
        "description": "获取内存统计信息",
        "parameters": {}
    }
    registry.register(
        name="memory_stats",
        toolset="system",
        schema=memory_stats_schema,
        handler=memory_stats_tool,
    )
    registered_tools.append("memory_stats")
    
    disk_usage_schema = {
        "description": "获取磁盘使用情况",
        "parameters": {}
    }
    registry.register(
        name="disk_usage",
        toolset="system",
        schema=disk_usage_schema,
        handler=disk_usage_tool,
    )
    registered_tools.append("disk_usage")
    
    top_processes_schema = {
        "description": "获取Top进程列表",
        "parameters": {}
    }
    registry.register(
        name="top_processes",
        toolset="system",
        schema=top_processes_schema,
        handler=top_processes_tool,
    )
    registered_tools.append("top_processes")
    
    ping_schema = {
        "description": "Ping测试",
        "parameters": {
            "host": {"type": "string", "description": "Host to ping", "default": "google.com"},
            "count": {"type": "integer", "description": "Number of pings", "default": 4}
        }
    }
    registry.register(
        name="ping",
        toolset="network",
        schema=ping_schema,
        handler=ping_tool,
    )
    registered_tools.append("ping")
    
    check_port_schema = {
        "description": "检查端口是否开放",
        "parameters": {
            "host": {"type": "string", "description": "Host to check", "default": "localhost"},
            "port": {"type": "integer", "description": "Port to check", "default": 80}
        }
    }
    registry.register(
        name="check_port",
        toolset="network",
        schema=check_port_schema,
        handler=check_port_tool,
    )
    registered_tools.append("check_port")
    
    dns_lookup_schema = {
        "description": "DNS查询",
        "parameters": {
            "domain": {"type": "string", "description": "Domain to lookup", "default": "example.com"},
            "record_type": {"type": "string", "description": "Record type", "default": "A"}
        }
    }
    registry.register(
        name="dns_lookup",
        toolset="network",
        schema=dns_lookup_schema,
        handler=dns_lookup_tool,
    )
    registered_tools.append("dns_lookup")
    
    curl_check_schema = {
        "description": "HTTP请求测试",
        "parameters": {
            "url": {"type": "string", "description": "URL to test", "default": "https://example.com"}
        }
    }
    registry.register(
        name="curl_check",
        toolset="network",
        schema=curl_check_schema,
        handler=curl_check_tool,
    )
    registered_tools.append("curl_check")
    
    read_log_schema = {
        "description": "读取日志文件",
        "parameters": {
            "file_path": {"type": "string", "description": "Path to log file"},
            "lines": {"type": "integer", "description": "Number of lines to read", "default": 100}
        },
        "required": ["file_path"]
    }
    registry.register(
        name="read_log",
        toolset="logs",
        schema=read_log_schema,
        handler=read_log_tool,
    )
    registered_tools.append("read_log")
    
    log_errors_schema = {
        "description": "查找日志中的错误",
        "parameters": {
            "file_path": {"type": "string", "description": "Path to log file"}
        },
        "required": ["file_path"]
    }
    registry.register(
        name="log_errors",
        toolset="logs",
        schema=log_errors_schema,
        handler=log_errors_tool,
    )
    registered_tools.append("log_errors")
    
    analyze_log_schema = {
        "description": "分析日志文件",
        "parameters": {
            "file_path": {"type": "string", "description": "Path to log file"}
        },
        "required": ["file_path"]
    }
    registry.register(
        name="analyze_log",
        toolset="logs",
        schema=analyze_log_schema,
        handler=analyze_log_tool,
    )
    registered_tools.append("analyze_log")
    
    return registered_tools


register_ops_tools()
