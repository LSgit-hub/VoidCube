#!/usr/bin/env python3
"""
UI Automation Tool Module

Provides comprehensive tools for controlling Windows desktop applications using the uiautomation library.
Supports intelligent control scanning, conditional waiting, and robust automation workflows.

Requires: pip install uiautomation
"""

import json
import shlex
import subprocess
import time
import re
from typing import Optional, Dict, Any, List

try:
    import uiautomation as auto
    AUTO_AVAILABLE = True
except ImportError:
    AUTO_AVAILABLE = False

from tools.registry import tool_error


def _ensure_automation_available():
    if not AUTO_AVAILABLE:
        return tool_error("uiautomation library not installed. Please run: pip install uiautomation")
    return None


def uia_find_window(
    name: Optional[str] = None,
    class_name: Optional[str] = None,
    process_id: Optional[int] = None,
    search_depth: int = 1,
    regex_match: bool = False
) -> str:
    error = _ensure_automation_available()
    if error:
        return error
    
    try:
        windows = []
        
        def find_windows(parent, depth):
            if depth > search_depth:
                return
            for child in parent.GetChildren():
                if isinstance(child, auto.WindowControl):
                    match = True
                    if name:
                        if regex_match:
                            if not re.search(name, child.Name, re.IGNORECASE):
                                match = False
                        else:
                            if name not in child.Name:
                                match = False
                    if class_name and child.ClassName != class_name:
                        match = False
                    if process_id and child.ProcessId != process_id:
                        match = False
                    if match:
                        windows.append({
                            "name": child.Name,
                            "class_name": child.ClassName,
                            "process_id": child.ProcessId,
                            "automation_id": child.AutomationId,
                            "rectangle": {
                                "left": child.BoundingRectangle.left,
                                "top": child.BoundingRectangle.top,
                                "width": child.BoundingRectangle.width,
                                "height": child.BoundingRectangle.height
                            }
                        })
                find_windows(child, depth + 1)
        
        find_windows(auto.GetRootControl(), 0)
        
        if windows:
            result = {
                "success": True,
                "found": True,
                "count": len(windows),
                "windows": windows
            }
        else:
            result = {"success": True, "found": False, "message": "Window not found"}
        
        return json.dumps(result, ensure_ascii=False)
    
    except Exception as e:
        return tool_error(f"Error finding window: {str(e)}")


def uia_wait_for_window(
    name: Optional[str] = None,
    class_name: Optional[str] = None,
    timeout: int = 30,
    interval: float = 0.5
) -> str:
    error = _ensure_automation_available()
    if error:
        return error
    
    try:
        start_time = time.time()
        while time.time() - start_time < timeout:
            result = json.loads(uia_find_window(name, class_name, search_depth=2))
            if result.get("found") and result.get("count", 0) > 0:
                return json.dumps({
                    "success": True,
                    "message": "Window found",
                    "window": result["windows"][0],
                    "wait_time": round(time.time() - start_time, 2)
                }, ensure_ascii=False)
            time.sleep(interval)
        
        return tool_error(f"Window not found within {timeout} seconds")
    
    except Exception as e:
        return tool_error(f"Error waiting for window: {str(e)}")


def uia_search_controls(
    window_name: str,
    control_type: Optional[str] = None,
    name_pattern: Optional[str] = None,
    automation_id_pattern: Optional[str] = None,
    search_depth: int = 3
) -> str:
    error = _ensure_automation_available()
    if error:
        return error
    
    try:
        window = auto.WindowControl(Name=window_name, searchDepth=1)
        if not window.Exists():
            return tool_error(f"Window '{window_name}' not found")
        
        matches = []
        
        def search(control, depth):
            if depth > search_depth:
                return
            
            try:
                match = True
                if control_type:
                    if not re.search(control_type, control.ControlTypeName, re.IGNORECASE):
                        match = False
                if name_pattern:
                    if not re.search(name_pattern, control.Name, re.IGNORECASE):
                        match = False
                if automation_id_pattern:
                    if not re.search(automation_id_pattern, control.AutomationId, re.IGNORECASE):
                        match = False
                
                if match:
                    matches.append({
                        "type": control.ControlTypeName,
                        "name": control.Name,
                        "automation_id": control.AutomationId,
                        "class_name": control.ClassName,
                        "depth": depth
                    })
            except (AttributeError, OSError):
                pass
            
            for child in control.GetChildren():
                search(child, depth + 1)
        
        search(window, 0)
        
        result = {
            "success": True,
            "count": len(matches),
            "controls": matches
        }
        return json.dumps(result, ensure_ascii=False)
    
    except Exception as e:
        return tool_error(f"Error searching controls: {str(e)}")


def uia_click_button(
    window_name: str,
    button_name: Optional[str] = None,
    automation_id: Optional[str] = None,
    search_depth: int = 3,
    retries: int = 3,
    retry_delay: float = 1.0
) -> str:
    error = _ensure_automation_available()
    if error:
        return error
    
    for attempt in range(retries):
        try:
            window = auto.WindowControl(Name=window_name, searchDepth=1)
            if not window.Exists():
                if attempt < retries - 1:
                    time.sleep(retry_delay)
                    continue
                return tool_error(f"Window '{window_name}' not found")
            
            button = None
            if automation_id:
                button = window.ButtonControl(AutomationId=automation_id, searchDepth=search_depth)
            elif button_name:
                button = window.ButtonControl(Name=button_name, searchDepth=search_depth)
            
            if button and button.Exists():
                button.Click()
                return json.dumps({"success": True, "message": "Button clicked successfully", "attempts": attempt + 1})
            else:
                if attempt < retries - 1:
                    time.sleep(retry_delay)
                    continue
                return tool_error("Button not found")
        
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(retry_delay)
                continue
            return tool_error(f"Error clicking button: {str(e)}")
    
    return tool_error("Failed after retries")


def uia_set_text(
    window_name: str,
    text: str,
    control_name: Optional[str] = None,
    automation_id: Optional[str] = None,
    search_depth: int = 3,
    clear_first: bool = True
) -> str:
    error = _ensure_automation_available()
    if error:
        return error
    
    try:
        window = auto.WindowControl(Name=window_name, searchDepth=1)
        if not window.Exists():
            return tool_error(f"Window '{window_name}' not found")
        
        edit = None
        if automation_id:
            edit = window.EditControl(AutomationId=automation_id, searchDepth=search_depth)
        elif control_name:
            edit = window.EditControl(Name=control_name, searchDepth=search_depth)
        else:
            edit = window.EditControl(searchDepth=search_depth)
        
        if edit and edit.Exists():
            if clear_first:
                edit.SendKeys("{CTRL}a{DELETE}")
            edit.SendKeys(text)
            return json.dumps({"success": True, "message": "Text input successful"})
        else:
            return tool_error("Edit control not found")
    
    except Exception as e:
        return tool_error(f"Error setting text: {str(e)}")


def uia_get_text(
    window_name: str,
    control_name: Optional[str] = None,
    automation_id: Optional[str] = None,
    search_depth: int = 3
) -> str:
    error = _ensure_automation_available()
    if error:
        return error
    
    try:
        window = auto.WindowControl(Name=window_name, searchDepth=1)
        if not window.Exists():
            return tool_error(f"Window '{window_name}' not found")
        
        text_control = None
        if automation_id:
            text_control = window.TextControl(AutomationId=automation_id, searchDepth=search_depth)
        elif control_name:
            text_control = window.TextControl(Name=control_name, searchDepth=search_depth)
        
        if text_control and text_control.Exists():
            result = {
                "success": True,
                "text": text_control.Name
            }
            return json.dumps(result, ensure_ascii=False)
        else:
            return tool_error("Text control not found")
    
    except Exception as e:
        return tool_error(f"Error getting text: {str(e)}")


def uia_list_controls(
    window_name: str,
    search_depth: int = 2,
    include_hidden: bool = False
) -> str:
    error = _ensure_automation_available()
    if error:
        return error
    
    try:
        window = auto.WindowControl(Name=window_name, searchDepth=1)
        if not window.Exists():
            return tool_error(f"Window '{window_name}' not found")
        
        controls = []
        
        def enumerate_controls(control, depth=0):
            if depth > search_depth:
                return
            
            try:
                if not include_hidden and not control.IsOffscreen:
                    control_info = {
                        "type": control.ControlTypeName,
                        "name": control.Name,
                        "automation_id": control.AutomationId,
                        "class_name": control.ClassName,
                        "depth": depth,
                        "is_enabled": control.IsEnabled,
                        "is_visible": not control.IsOffscreen
                    }
                    controls.append(control_info)
            except (AttributeError, OSError):
                pass
            
            for child in control.GetChildren():
                enumerate_controls(child, depth + 1)
        
        enumerate_controls(window, 0)
        
        result = {
            "success": True,
            "count": len(controls),
            "controls": controls
        }
        return json.dumps(result, ensure_ascii=False)
    
    except Exception as e:
        return tool_error(f"Error listing controls: {str(e)}")


def uia_select_combo_item(
    window_name: str,
    combo_name: Optional[str] = None,
    automation_id: Optional[str] = None,
    item_name: str = None,
    item_index: int = None,
    search_depth: int = 3
) -> str:
    error = _ensure_automation_available()
    if error:
        return error
    
    try:
        window = auto.WindowControl(Name=window_name, searchDepth=1)
        if not window.Exists():
            return tool_error(f"Window '{window_name}' not found")
        
        combo = None
        if automation_id:
            combo = window.ComboBoxControl(AutomationId=automation_id, searchDepth=search_depth)
        elif combo_name:
            combo = window.ComboBoxControl(Name=combo_name, searchDepth=search_depth)
        
        if combo and combo.Exists():
            combo.Click()
            time.sleep(0.3)
            
            if item_name:
                combo.Select(item_name)
            elif item_index is not None:
                items = combo.GetChildren()
                if 0 <= item_index < len(items):
                    items[item_index].Click()
                else:
                    return tool_error(f"Item index {item_index} out of range")
            
            return json.dumps({"success": True, "message": "Combo selection successful"})
        else:
            return tool_error("ComboBox control not found")
    
    except Exception as e:
        return tool_error(f"Error selecting combo item: {str(e)}")


def uia_toggle_checkbox(
    window_name: str,
    checkbox_name: Optional[str] = None,
    automation_id: Optional[str] = None,
    checked: Optional[bool] = None,
    search_depth: int = 3
) -> str:
    error = _ensure_automation_available()
    if error:
        return error
    
    try:
        window = auto.WindowControl(Name=window_name, searchDepth=1)
        if not window.Exists():
            return tool_error(f"Window '{window_name}' not found")
        
        checkbox = None
        if automation_id:
            checkbox = window.CheckBoxControl(AutomationId=automation_id, searchDepth=search_depth)
        elif checkbox_name:
            checkbox = window.CheckBoxControl(Name=checkbox_name, searchDepth=search_depth)
        
        if checkbox and checkbox.Exists():
            current_state = checkbox.IsChecked
            if checked is None:
                checkbox.Click()
                new_state = not current_state
            elif checked != current_state:
                checkbox.Click()
                new_state = checked
            else:
                new_state = current_state
            
            return json.dumps({
                "success": True,
                "message": "Checkbox toggled",
                "new_state": new_state
            }, ensure_ascii=False)
        else:
            return tool_error("Checkbox control not found")
    
    except Exception as e:
        return tool_error(f"Error toggling checkbox: {str(e)}")


def uia_send_keys(
    window_name: str,
    keys: str,
    delay: float = 0.0
) -> str:
    error = _ensure_automation_available()
    if error:
        return error
    
    try:
        window = auto.WindowControl(Name=window_name, searchDepth=1)
        if not window.Exists():
            return tool_error(f"Window '{window_name}' not found")
        
        window.SetFocus()
        if delay > 0:
            time.sleep(delay)
        auto.SendKeys(keys)
        return json.dumps({"success": True, "message": "Keys sent successfully"})
    
    except Exception as e:
        return tool_error(f"Error sending keys: {str(e)}")


def uia_window_action(
    window_name: str,
    action: str
) -> str:
    error = _ensure_automation_available()
    if error:
        return error
    
    try:
        window = auto.WindowControl(Name=window_name, searchDepth=1)
        if not window.Exists():
            return tool_error(f"Window '{window_name}' not found")
        
        action = action.lower()
        if action == "maximize":
            window.Maximize()
        elif action == "minimize":
            window.Minimize()
        elif action == "close":
            window.Close()
        elif action == "activate":
            window.SetFocus()
        elif action == "restore":
            window.Restore()
        elif action == "get_info":
            return json.dumps({
                "success": True,
                "name": window.Name,
                "class_name": window.ClassName,
                "process_id": window.ProcessId,
                "rectangle": {
                    "left": window.BoundingRectangle.left,
                    "top": window.BoundingRectangle.top,
                    "width": window.BoundingRectangle.width,
                    "height": window.BoundingRectangle.height
                },
                "is_maximized": window.IsMaximized,
                "is_minimized": window.IsMinimized,
                "is_enabled": window.IsEnabled
            }, ensure_ascii=False)
        else:
            return tool_error(f"Unknown action: {action}")
        
        return json.dumps({"success": True, "message": f"Window {action}d successfully"})
    
    except Exception as e:
        return tool_error(f"Error performing window action: {str(e)}")


def uia_mouse_click(
    x: int,
    y: int,
    double_click: bool = False
) -> str:
    error = _ensure_automation_available()
    if error:
        return error
    
    try:
        if double_click:
            auto.Click(x, y, double=True)
        else:
            auto.Click(x, y)
        
        return json.dumps({"success": True, "message": "Mouse click performed"})
    
    except Exception as e:
        return tool_error(f"Error performing mouse click: {str(e)}")


def uia_mouse_move(
    x: int,
    y: int
) -> str:
    error = _ensure_automation_available()
    if error:
        return error
    
    try:
        auto.MoveTo(x, y)
        return json.dumps({"success": True, "message": "Mouse moved"})
    
    except Exception as e:
        return tool_error(f"Error moving mouse: {str(e)}")


def uia_start_process(
    executable: str,
    arguments: Optional[str] = None,
    wait_for_window: bool = False,
    timeout: int = 5
) -> str:
    try:
        if arguments:
            process = subprocess.Popen([executable] + shlex.split(arguments))
        else:
            process = subprocess.Popen([executable])
        
        result = {
            "success": True,
            "process_id": process.pid,
            "message": "Process started successfully"
        }
        
        if wait_for_window and AUTO_AVAILABLE:
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    window = auto.WindowControl(ProcessId=process.pid, searchDepth=1)
                    if window.Exists():
                        result["window_found"] = True
                        result["window_name"] = window.Name
                        break
                except (AttributeError, OSError):
                    pass
                time.sleep(0.5)
            else:
                result["window_found"] = False
        
        return json.dumps(result, ensure_ascii=False)
    
    except Exception as e:
        return tool_error(f"Error starting process: {str(e)}")


def uia_wait(
    seconds: float = 1.0
) -> str:
    try:
        time.sleep(seconds)
        return json.dumps({"success": True, "message": f"Waited {seconds} seconds"})
    except Exception as e:
        return tool_error(f"Error waiting: {str(e)}")


def uia_get_control_info(
    window_name: str,
    control_name: Optional[str] = None,
    automation_id: Optional[str] = None,
    search_depth: int = 3
) -> str:
    error = _ensure_automation_available()
    if error:
        return error
    
    try:
        window = auto.WindowControl(Name=window_name, searchDepth=1)
        if not window.Exists():
            return tool_error(f"Window '{window_name}' not found")
        
        control = None
        if automation_id:
            control = window.FindFirst(auto.ControlCondition(AutomationId=automation_id), searchDepth=search_depth)
        elif control_name:
            control = window.FindFirst(auto.ControlCondition(Name=control_name), searchDepth=search_depth)
        
        if control and control.Exists():
            result = {
                "success": True,
                "type": control.ControlTypeName,
                "name": control.Name,
                "automation_id": control.AutomationId,
                "class_name": control.ClassName,
                "is_enabled": control.IsEnabled,
                "is_visible": not control.IsOffscreen,
                "rectangle": {
                    "left": control.BoundingRectangle.left,
                    "top": control.BoundingRectangle.top,
                    "width": control.BoundingRectangle.width,
                    "height": control.BoundingRectangle.height
                },
                "has_focus": control.HasKeyboardFocus
            }
            return json.dumps(result, ensure_ascii=False)
        else:
            return tool_error("Control not found")
    
    except Exception as e:
        return tool_error(f"Error getting control info: {str(e)}")


def uia_scroll(
    window_name: str,
    direction: str = "down",
    amount: int = 1
) -> str:
    error = _ensure_automation_available()
    if error:
        return error
    
    try:
        window = auto.WindowControl(Name=window_name, searchDepth=1)
        if not window.Exists():
            return tool_error(f"Window '{window_name}' not found")
        
        window.SetFocus()
        
        direction = direction.lower()
        scroll_key = "{PGDN}" if direction == "down" else "{PGUP}"
        auto.SendKeys(scroll_key * amount)
        
        return json.dumps({"success": True, "message": f"Scrolled {direction} {amount} times"})
    
    except Exception as e:
        return tool_error(f"Error scrolling: {str(e)}")


TOOL_SCHEMAS = {
    "uia_find_window": {
        "description": "Find windows by name, class name, or process ID. Supports regex matching.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Window name (title)"},
                "class_name": {"type": "string", "description": "Window class name"},
                "process_id": {"type": "integer", "description": "Process ID"},
                "search_depth": {"type": "integer", "description": "Search depth", "default": 1},
                "regex_match": {"type": "boolean", "description": "Use regex matching for name", "default": False}
            },
            "required": []
        }
    },
    "uia_wait_for_window": {
        "description": "Wait for a window to appear (blocks until found or timeout)",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Window name to wait for"},
                "class_name": {"type": "string", "description": "Window class name"},
                "timeout": {"type": "integer", "description": "Maximum wait time in seconds", "default": 30},
                "interval": {"type": "number", "description": "Polling interval in seconds", "default": 0.5}
            },
            "required": ["name"]
        }
    },
    "uia_search_controls": {
        "description": "Search for controls matching specific criteria (type, name pattern, automation ID)",
        "parameters": {
            "type": "object",
            "properties": {
                "window_name": {"type": "string", "description": "Target window name", "required": True},
                "control_type": {"type": "string", "description": "Control type to filter (e.g., ButtonControl, EditControl)"},
                "name_pattern": {"type": "string", "description": "Regex pattern for control name"},
                "automation_id_pattern": {"type": "string", "description": "Regex pattern for automation ID"},
                "search_depth": {"type": "integer", "description": "Search depth", "default": 3}
            }
        }
    },
    "uia_click_button": {
        "description": "Click a button in a window with retry support",
        "parameters": {
            "type": "object",
            "properties": {
                "window_name": {"type": "string", "description": "Target window name", "required": True},
                "button_name": {"type": "string", "description": "Button name"},
                "automation_id": {"type": "string", "description": "Button automation ID"},
                "search_depth": {"type": "integer", "description": "Search depth", "default": 3},
                "retries": {"type": "integer", "description": "Number of retries", "default": 3},
                "retry_delay": {"type": "number", "description": "Delay between retries in seconds", "default": 1.0}
            }
        }
    },
    "uia_set_text": {
        "description": "Set text in an edit control",
        "parameters": {
            "type": "object",
            "properties": {
                "window_name": {"type": "string", "description": "Target window name", "required": True},
                "text": {"type": "string", "description": "Text to input", "required": True},
                "control_name": {"type": "string", "description": "Edit control name"},
                "automation_id": {"type": "string", "description": "Edit control automation ID"},
                "search_depth": {"type": "integer", "description": "Search depth", "default": 3},
                "clear_first": {"type": "boolean", "description": "Clear existing text first", "default": True}
            }
        }
    },
    "uia_get_text": {
        "description": "Get text from a control",
        "parameters": {
            "type": "object",
            "properties": {
                "window_name": {"type": "string", "description": "Target window name", "required": True},
                "control_name": {"type": "string", "description": "Control name"},
                "automation_id": {"type": "string", "description": "Control automation ID"},
                "search_depth": {"type": "integer", "description": "Search depth", "default": 3}
            }
        }
    },
    "uia_list_controls": {
        "description": "List all controls in a window with detailed information",
        "parameters": {
            "type": "object",
            "properties": {
                "window_name": {"type": "string", "description": "Target window name", "required": True},
                "search_depth": {"type": "integer", "description": "Search depth", "default": 2},
                "include_hidden": {"type": "boolean", "description": "Include hidden controls", "default": False}
            }
        }
    },
    "uia_select_combo_item": {
        "description": "Select an item from a ComboBox control",
        "parameters": {
            "type": "object",
            "properties": {
                "window_name": {"type": "string", "description": "Target window name", "required": True},
                "combo_name": {"type": "string", "description": "ComboBox name"},
                "automation_id": {"type": "string", "description": "ComboBox automation ID"},
                "item_name": {"type": "string", "description": "Item name to select"},
                "item_index": {"type": "integer", "description": "Item index to select"},
                "search_depth": {"type": "integer", "description": "Search depth", "default": 3}
            }
        }
    },
    "uia_toggle_checkbox": {
        "description": "Toggle a checkbox control",
        "parameters": {
            "type": "object",
            "properties": {
                "window_name": {"type": "string", "description": "Target window name", "required": True},
                "checkbox_name": {"type": "string", "description": "Checkbox name"},
                "automation_id": {"type": "string", "description": "Checkbox automation ID"},
                "checked": {"type": "boolean", "description": "Target state (None = toggle)"},
                "search_depth": {"type": "integer", "description": "Search depth", "default": 3}
            }
        }
    },
    "uia_send_keys": {
        "description": "Send keyboard keys to a window",
        "parameters": {
            "type": "object",
            "properties": {
                "window_name": {"type": "string", "description": "Target window name", "required": True},
                "keys": {"type": "string", "description": "Keys to send (supports {ENTER}, {TAB}, {BACKSPACE}, {CTRL}s, etc.)", "required": True},
                "delay": {"type": "number", "description": "Delay before sending keys", "default": 0}
            }
        }
    },
    "uia_window_action": {
        "description": "Perform window actions (maximize, minimize, close, activate, restore, get_info)",
        "parameters": {
            "type": "object",
            "properties": {
                "window_name": {"type": "string", "description": "Target window name", "required": True},
                "action": {"type": "string", "description": "Action to perform", "required": True}
            }
        }
    },
    "uia_mouse_click": {
        "description": "Perform a mouse click at specified coordinates",
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate", "required": True},
                "y": {"type": "integer", "description": "Y coordinate", "required": True},
                "double_click": {"type": "boolean", "description": "Double click", "default": False}
            }
        }
    },
    "uia_mouse_move": {
        "description": "Move mouse cursor to specified coordinates",
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate", "required": True},
                "y": {"type": "integer", "description": "Y coordinate", "required": True}
            }
        }
    },
    "uia_start_process": {
        "description": "Start a process and optionally wait for its window",
        "parameters": {
            "type": "object",
            "properties": {
                "executable": {"type": "string", "description": "Path to executable", "required": True},
                "arguments": {"type": "string", "description": "Command line arguments"},
                "wait_for_window": {"type": "boolean", "description": "Wait for window to appear", "default": False},
                "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 5}
            }
        }
    },
    "uia_wait": {
        "description": "Wait for specified number of seconds",
        "parameters": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "Number of seconds to wait", "default": 1.0}
            }
        }
    },
    "uia_get_control_info": {
        "description": "Get detailed information about a specific control",
        "parameters": {
            "type": "object",
            "properties": {
                "window_name": {"type": "string", "description": "Target window name", "required": True},
                "control_name": {"type": "string", "description": "Control name"},
                "automation_id": {"type": "string", "description": "Control automation ID"},
                "search_depth": {"type": "integer", "description": "Search depth", "default": 3}
            }
        }
    },
    "uia_scroll": {
        "description": "Scroll window content up or down",
        "parameters": {
            "type": "object",
            "properties": {
                "window_name": {"type": "string", "description": "Target window name", "required": True},
                "direction": {"type": "string", "description": "Scroll direction (up/down)", "default": "down"},
                "amount": {"type": "integer", "description": "Number of page scrolls", "default": 1}
            }
        }
    }
}


def register_tools():
    from tools.registry import registry

    availability = lambda: AUTO_AVAILABLE
    
    registry.register(
        name="uia_find_window",
        toolset="uiautomation",
        schema=TOOL_SCHEMAS["uia_find_window"],
        handler=uia_find_window,
        check_fn=availability,
    )
    registry.register(
        name="uia_wait_for_window",
        toolset="uiautomation",
        schema=TOOL_SCHEMAS["uia_wait_for_window"],
        handler=uia_wait_for_window,
        check_fn=availability,
    )
    registry.register(
        name="uia_search_controls",
        toolset="uiautomation",
        schema=TOOL_SCHEMAS["uia_search_controls"],
        handler=uia_search_controls,
        check_fn=availability,
    )
    registry.register(
        name="uia_click_button",
        toolset="uiautomation",
        schema=TOOL_SCHEMAS["uia_click_button"],
        handler=uia_click_button,
        check_fn=availability,
    )
    registry.register(
        name="uia_set_text",
        toolset="uiautomation",
        schema=TOOL_SCHEMAS["uia_set_text"],
        handler=uia_set_text,
        check_fn=availability,
    )
    registry.register(
        name="uia_get_text",
        toolset="uiautomation",
        schema=TOOL_SCHEMAS["uia_get_text"],
        handler=uia_get_text,
        check_fn=availability,
    )
    registry.register(
        name="uia_list_controls",
        toolset="uiautomation",
        schema=TOOL_SCHEMAS["uia_list_controls"],
        handler=uia_list_controls,
        check_fn=availability,
    )
    registry.register(
        name="uia_select_combo_item",
        toolset="uiautomation",
        schema=TOOL_SCHEMAS["uia_select_combo_item"],
        handler=uia_select_combo_item,
        check_fn=availability,
    )
    registry.register(
        name="uia_toggle_checkbox",
        toolset="uiautomation",
        schema=TOOL_SCHEMAS["uia_toggle_checkbox"],
        handler=uia_toggle_checkbox,
        check_fn=availability,
    )
    registry.register(
        name="uia_send_keys",
        toolset="uiautomation",
        schema=TOOL_SCHEMAS["uia_send_keys"],
        handler=uia_send_keys,
        check_fn=availability,
    )
    registry.register(
        name="uia_window_action",
        toolset="uiautomation",
        schema=TOOL_SCHEMAS["uia_window_action"],
        handler=uia_window_action,
        check_fn=availability,
    )
    registry.register(
        name="uia_mouse_click",
        toolset="uiautomation",
        schema=TOOL_SCHEMAS["uia_mouse_click"],
        handler=uia_mouse_click,
        check_fn=availability,
    )
    registry.register(
        name="uia_mouse_move",
        toolset="uiautomation",
        schema=TOOL_SCHEMAS["uia_mouse_move"],
        handler=uia_mouse_move,
        check_fn=availability,
    )
    registry.register(
        name="uia_start_process",
        toolset="uiautomation",
        schema=TOOL_SCHEMAS["uia_start_process"],
        handler=uia_start_process,
        check_fn=availability,
    )
    registry.register(
        name="uia_wait",
        toolset="uiautomation",
        schema=TOOL_SCHEMAS["uia_wait"],
        handler=uia_wait,
        check_fn=availability,
    )
    registry.register(
        name="uia_get_control_info",
        toolset="uiautomation",
        schema=TOOL_SCHEMAS["uia_get_control_info"],
        handler=uia_get_control_info,
        check_fn=availability,
    )
    registry.register(
        name="uia_scroll",
        toolset="uiautomation",
        schema=TOOL_SCHEMAS["uia_scroll"],
        handler=uia_scroll,
        check_fn=availability,
    )


register_tools()
