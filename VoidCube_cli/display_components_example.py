#!/usr/bin/env python3
"""
显示组件使用示例
演示如何使用 api_config.py 中的 DisplayComponents
"""

from VoidCube_cli.api_config import DisplayComponents, dc


def example_basic_colors():
    """基础颜色示例"""
    print("=== 基础颜色示例 ===")
    print(dc.colored("红色文本", "red"))
    print(dc.colored("绿色文本", "green"))
    print(dc.colored("黄色文本", "yellow"))
    print(dc.colored("蓝色文本", "blue"))
    print(dc.colored("品红色文本", "magenta"))
    print(dc.colored("青色文本", "cyan"))
    print(dc.colored("加粗文本", "white", bold=True))
    print()


def example_separator():
    """分隔线示例"""
    print("=== 分隔线示例 ===")
    print(dc.separator(40))
    print(dc.separator(50, char='-', color='cyan'))
    print(dc.separator(60, char='=', color='magenta'))
    print()


def example_header():
    """标题框示例"""
    print("=== 标题框示例 ===")
    print(dc.header("欢迎使用", width=40))
    print(dc.header("带边框的标题\n第二行", width=50, border_style='double', color='yellow'))
    print(dc.header("粗边框标题", width=45, border_style='bold', color='green'))
    print()


def example_progress_bar():
    """进度条示例"""
    print("=== 进度条示例 ===")
    
    # 经典样式
    print(dc.progress_bar(25, 100, style='classic'))
    print(dc.progress_bar(50, 100, style='modern', color='cyan'))
    print(dc.progress_bar(75, 100, style='dots', color='magenta'))
    
    # 带前缀
    print(dc.progress_bar(30, 100, prefix="下载中", color='blue'))
    
    # 只显示百分比
    print(dc.progress_bar(60, 100, show_count=False, color='yellow'))
    print()


def example_table():
    """表格示例"""
    print("=== 表格示例 ===")
    
    # 简单表格
    data = [
        ["项目1", "值1", "描述1"],
        ["项目2", "值2", "描述2"],
        ["项目3", "值3", "描述3"],
    ]
    headers = ["名称", "数值", "描述"]
    
    print(dc.table(data, headers, border_style='rounded'))
    print()
    
    # 双色行
    print(dc.table(data, headers, border_style='double', 
                  row_colors=['white', 'dim'], header_color='green'))
    print()


def example_list():
    """列表示例"""
    print("=== 列表示例 ===")
    
    items = ["第一个项目", "第二个项目", "第三个项目", "第四个项目"]
    
    print("无序列表:")
    print(dc.list_items(items, bullet='•', color='cyan'))
    print()
    
    print("有序列表:")
    print(dc.list_items(items, numbered=True, color='green'))
    print()


def example_key_value():
    """键值对示例"""
    print("=== 键值对示例 ===")
    
    config = {
        "API Key": "sk-xxxx-xxxx",
        "Provider": "OpenRouter",
        "Model": "gpt-4",
        "Temperature": 0.7,
        "Max Tokens": 2000,
    }
    
    print(dc.key_value(config, key_color='yellow', value_color='cyan'))
    print()


def example_messages():
    """消息示例"""
    print("=== 消息示例 ===")
    
    print(dc.success("操作成功完成！"))
    print(dc.error("发生了一个错误"))
    print(dc.warning("请注意这个警告"))
    print(dc.info("这是一条信息"))
    print()


def example_highlight():
    """高亮示例"""
    print("=== 高亮示例 ===")
    
    text = "这是一段包含重要信息的文本，需要高亮显示"
    highlighted = dc.highlight(text, "重要信息", highlight_color='yellow')
    print(highlighted)
    print()


def example_tree():
    """树形结构示例"""
    print("=== 树形结构示例 ===")
    
    tree_data = {
        "项目根目录": {
            "src": {
                "main.py": "主程序文件",
                "utils.py": "工具函数",
            },
            "tests": {
                "test_main.py": None,
            },
            "README.md": "项目说明",
        }
    }
    
    print(dc.tree(tree_data))
    print()


def example_git_info():
    """Git 信息示例"""
    print("=== Git 仓库信息示例 ===")
    
    git_info = dc.git_info('.', show_details=True)
    print(git_info)
    print()


def example_comprehensive():
    """综合示例"""
    print("=== 综合示例 ===")
    
    # 显示一个完整的配置界面
    print(dc.header("系统配置面板", width=50, color='cyan'))
    print()
    
    config_data = {
        "用户名": "admin",
        "主机": "localhost",
        "端口": "8080",
        "状态": "运行中",
    }
    
    print(dc.key_value(config_data))
    print()
    
    print(dc.separator(50, color='dim'))
    print(dc.info("正在加载模块..."))
    print(dc.progress_bar(75, 100, style='modern', prefix="进度"))
    print(dc.success("所有模块加载完成！"))
    print()


if __name__ == "__main__":
    example_basic_colors()
    example_separator()
    example_header()
    example_progress_bar()
    example_table()
    example_list()
    example_key_value()
    example_messages()
    example_highlight()
    example_tree()
    example_git_info()
    example_comprehensive()
    
    print(dc.header("使用说明", width=50))
    print("\n如何使用显示组件:")
    print("1. 导入: from VoidCube_cli.api_config import dc")
    print("2. 调用: print(dc.header('标题'))")
    print("3. 或者直接使用 DisplayComponents 类")
    print()
    print("可扩展点:")
    print("- 在 DisplayComponents 类中添加新的静态方法")
    print("- 自定义颜色、边框、进度条样式")
    print("- 创建自己的显示组件子类")
