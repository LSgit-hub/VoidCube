"""
Canonical tool registry

本，移除复杂的工具注册逻辑。
"""

from typing import Dict, Any, List, Optional
import json
import inspect
import asyncio


def tool_error(message: str, **kwargs) -> str:
    """生成工具错误响应"""
    result = {"success": False, "error": message}
    result.update(kwargs)
    return json.dumps(result, ensure_ascii=False)


class ToolRegistry:
    """工具注册表"""
    
    def __init__(self):
        self._tools: Dict[str, Any] = {}
        self._toolsets: Dict[str, List[str]] = {}
        self._is_async: Dict[str, bool] = {}
        self._schemas: Dict[str, Any] = {}
        self._check_fns: Dict[str, Any] = {}
        self._effects: Dict[str, str] = {}
    
    def register(self, name: str, tool: Any = None, toolset: str = None, 
                 schema: Any = None, handler: Any = None, check_fn: Any = None,
                 emoji: str = None, max_result_size_chars: int = None,
                 is_async: bool = False,
                 effect: str = "non_idempotent_write",
                 **kwargs) -> None:
        """注册工具
        
        Args:
            name: 工具名称
            tool: 工具对象（可选）
            toolset: 工具集名称
            schema: 工具schema
            handler: 工具处理函数（用作tool）
            check_fn: 工具可用性检查函数
            emoji: 表情符号（忽略）
            max_result_size_chars: 最大结果大小（忽略）
            is_async: 工具是否为异步函数
            **kwargs: 其他参数（忽略）
        """
        # 如果没有提供tool但有handler，则使用handler作为tool
        if tool is None and handler is not None:
            tool = handler
        elif tool is None:
            tool = {}
        
        self._tools[name] = tool
        self._is_async[name] = is_async
        if effect not in {"read_only", "idempotent_write", "non_idempotent_write"}:
            raise ValueError(f"Unsupported tool effect: {effect}")
        self._effects[name] = effect
        if check_fn is None:
            self._check_fns.pop(name, None)
        else:
            self._check_fns[name] = check_fn
        if schema is not None:
            self._schemas[name] = schema
        if toolset:
            if toolset not in self._toolsets:
                self._toolsets[toolset] = []
            if name not in self._toolsets[toolset]:
                self._toolsets[toolset].append(name)
    
    def get(self, name: str) -> Optional[Any]:
        """获取工具"""
        return self._tools.get(name)

    def get_effect(self, name: str) -> str:
        return self._effects.get(name, "non_idempotent_write")
    
    def list_tools(self) -> List[str]:
        """列出所有工具"""
        return list(self._tools.keys())
    
    def has_tool(self, name: str) -> bool:
        """检查工具是否存在"""
        return name in self._tools
    
    def get_tool_to_toolset_map(self) -> Dict[str, str]:
        """获取工具到工具集的映射"""
        result = {}
        for toolset, tools in self._toolsets.items():
            for tool in tools:
                result[tool] = toolset
        return result
    
    def register_toolset(self, name: str, tools: List[str]) -> None:
        """注册工具集"""
        self._toolsets[name] = tools
    
    def get_toolset_tools(self, name: str) -> List[str]:
        """获取工具集中的工具"""
        return self._toolsets.get(name, [])
    
    def list_toolsets(self) -> List[str]:
        """列出所有工具集"""
        return list(self._toolsets.keys())
    
    def get_toolset_requirements(self) -> Dict[str, bool]:
        """Return availability for toolsets that contain registered tools."""
        requirements: Dict[str, bool] = {}
        availability = self._availability_for(self.list_tools())
        for toolset_name, tools in self._toolsets.items():
            registered = [name for name in tools if name in self._tools]
            if registered:
                requirements[toolset_name] = all(
                    availability.get(name, False) for name in registered
                )
        return requirements

    def _is_tool_available(self, name: str) -> bool:
        check_fn = self._check_fns.get(name)
        if check_fn is None:
            return name in self._tools
        try:
            return bool(check_fn())
        except Exception:
            return False

    def _availability_for(self, names: List[str]) -> Dict[str, bool]:
        """Evaluate each shared availability check at most once per scan."""
        results: Dict[str, bool] = {}
        by_check: Dict[int, bool] = {}
        for name in names:
            check_fn = self._check_fns.get(name)
            if check_fn is None:
                results[name] = name in self._tools
                continue
            cache_key = id(check_fn)
            if cache_key not in by_check:
                try:
                    by_check[cache_key] = bool(check_fn())
                except Exception:
                    by_check[cache_key] = False
            results[name] = by_check[cache_key]
        return results
    
    def get_definitions(self, tool_names: List[str] = None, quiet: bool = False) -> List[Dict[str, Any]]:
        """获取工具定义（schema）
        
        Args:
            tool_names: 要包含的工具名称列表，None表示所有工具
            quiet: 安静模式，不打印警告
        
        Returns:
            工具定义列表，格式为 [{"type": "function", "function": {...}}, ...]
        """
        definitions = []
        
        if tool_names is None:
            tool_names = self.list_tools()
        
        availability = self._availability_for(tool_names)
        for name in tool_names:
            tool = self.get(name)
            if tool is None or not availability.get(name, False):
                continue
            
            # 构建工具定义
            tool_def = None
            
            # 如果工具是字典，检查是否有schema字段
            if isinstance(tool, dict):
                if "schema" in tool:
                    schema = tool["schema"]
                    if isinstance(schema, dict):
                        tool_def = {
                            "type": "function",
                            "function": {
                                "name": name,
                                **schema
                            }
                        }
                elif "definition" in tool:
                    tool_def = tool["definition"]
            elif name in self._schemas:
                # 使用独立存储的schema
                schema = self._schemas[name]
                if isinstance(schema, dict):
                    tool_def = {
                        "type": "function",
                        "function": {
                            "name": name,
                            **schema
                        }
                    }
            elif callable(tool):
                tool_def = {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": f"Tool: {name}",
                        "parameters": {"type": "object", "properties": {}}
                    }
                }
            
            # 如果没有生成定义，使用默认格式
            if tool_def is None:
                tool_def = {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": f"Tool: {name}",
                        "parameters": {"type": "object", "properties": {}}
                    }
                }

            tool_def = self._normalize_tool_definition(tool_def, fallback_name=name)
            
            definitions.append(tool_def)
        
        return definitions

    def normalize_tool_definition(
        self,
        tool_def: Dict[str, Any],
        fallback_name: str,
    ) -> Dict[str, Any]:
        """Public wrapper for strict function-tool normalization."""
        return self._normalize_tool_definition(tool_def, fallback_name=fallback_name)

    def normalize_parameters_schema(
        self,
        params: Any,
        *,
        legacy_required: Any = None,
    ) -> Dict[str, Any]:
        """Public wrapper for parameter schema normalization."""
        return self._normalize_parameters_schema(params, legacy_required=legacy_required)

    def _normalize_tool_definition(
        self,
        tool_def: Dict[str, Any],
        fallback_name: str,
    ) -> Dict[str, Any]:
        """Normalize legacy tool schemas into strict function-calling JSON Schema.

        Older VoidCube tools often define:
        - ``parameters`` as a flat dict of fields instead of a JSON Schema object
        - ``required`` alongside ``parameters`` instead of inside it
        - empty ``parameters`` as ``{}``

        Strict providers like DeepSeek reject these shapes. Normalize them here
        so every downstream provider receives a valid ``type: object`` schema.
        """
        if not isinstance(tool_def, dict):
            return {
                "type": "function",
                "function": {
                    "name": fallback_name,
                    "description": f"Tool: {fallback_name}",
                    "parameters": {"type": "object", "properties": {}},
                },
            }

        normalized = dict(tool_def)
        if normalized.get("type") != "function":
            normalized["type"] = "function"

        function_block = normalized.get("function")
        if not isinstance(function_block, dict):
            function_block = {}
        function_block = dict(function_block)

        function_block["name"] = function_block.get("name") or fallback_name
        if not isinstance(function_block.get("description"), str):
            function_block["description"] = f"Tool: {fallback_name}"

        params = function_block.get("parameters")
        legacy_required = function_block.pop("required", None)
        function_block["parameters"] = self._normalize_parameters_schema(
            params,
            legacy_required=legacy_required,
        )

        normalized["function"] = function_block
        return normalized

    def _normalize_parameters_schema(
        self,
        params: Any,
        *,
        legacy_required: Any = None,
    ) -> Dict[str, Any]:
        """Convert mixed legacy parameter formats into JSON Schema objects."""
        if not isinstance(params, dict) or not params:
            schema: Dict[str, Any] = {"type": "object", "properties": {}}
            if isinstance(legacy_required, list) and legacy_required:
                schema["required"] = [str(item) for item in legacy_required]
            return schema

        # Already a proper JSON Schema object.
        if params.get("type") == "object":
            schema = dict(params)
            properties = schema.get("properties")
            if not isinstance(properties, dict):
                schema["properties"] = {}
            if "required" not in schema and isinstance(legacy_required, list) and legacy_required:
                schema["required"] = [str(item) for item in legacy_required]
            return schema

        # Legacy flat mapping: {"arg": {"type": "..."}}
        schema = {
            "type": "object",
            "properties": dict(params),
        }
        if isinstance(legacy_required, list) and legacy_required:
            schema["required"] = [str(item) for item in legacy_required]
        return schema
    
    def unregister(self, name: str) -> bool:
        """注销工具"""
        if name in self._tools:
            del self._tools[name]
            if name in self._is_async:
                del self._is_async[name]
            if name in self._schemas:
                del self._schemas[name]
            self._check_fns.pop(name, None)
            self._effects.pop(name, None)
            # 从工具集中移除
            for toolset in self._toolsets:
                if name in self._toolsets[toolset]:
                    self._toolsets[toolset].remove(name)
            return True
        return False
    
    def clear(self) -> None:
        """清空注册表"""
        self._tools.clear()
        self._toolsets.clear()
        self._is_async.clear()
        self._schemas.clear()
        self._check_fns.clear()
        self._effects.clear()
    
    def check_tool_availability(self, quiet: bool = False) -> tuple:
        """检查工具可用性
        
        Returns:
            (available_toolsets, unavailable_toolsets)
        """
        available: list = []
        unavailable: list = []
        availability = self._availability_for(self.list_tools())
        
        for toolset_name, tools in self._toolsets.items():
            registered = [name for name in tools if name in self._tools]
            available_tools = [
                name for name in registered if availability.get(name, False)
            ]
            unavailable_tools = [
                name for name in registered if name not in available_tools
            ]
            if available_tools:
                available.append({
                    "name": toolset_name,
                    "tools": available_tools,
                    "available": True,
                })
            if unavailable_tools:
                unavailable.append({
                    "name": toolset_name,
                    "tools": unavailable_tools,
                    "available": False,
                    "missing_vars": [],
                })
        
        return available, unavailable
    
    def get_toolset_for_tool(self, tool_name: str) -> Optional[str]:
        """获取工具所属的工具集
        
        Args:
            tool_name: 工具名称
        
        Returns:
            工具集名称，如果工具不存在则返回None
        """
        for toolset, tools in self._toolsets.items():
            if tool_name in tools:
                return toolset
        return None
    
    def dispatch(
        self,
        name: str,
        args: dict,
        *,
        raise_exceptions: bool = False,
        **kwargs,
    ) -> str:
        """分发工具调用
        
        Args:
            name: 工具名称
            args: 工具参数
            **kwargs: 其他参数
        
        Returns:
            工具执行结果
        """
        tool = self.get(name)
        if tool is None:
            return tool_error(f"Tool '{name}' not found")

        try:
            if callable(tool):
                sig = inspect.signature(tool)
                params = list(sig.parameters.keys())
                accepts_var_kwargs = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in sig.parameters.values()
                )

                def _filter_kwargs(candidate_kwargs: dict, skip_first_arg: bool = False) -> dict:
                    if accepts_var_kwargs:
                        return candidate_kwargs

                    allowed_names = set()
                    for index, param in enumerate(sig.parameters.values()):
                        if skip_first_arg and index == 0:
                            continue
                        if param.kind in (
                            inspect.Parameter.POSITIONAL_OR_KEYWORD,
                            inspect.Parameter.KEYWORD_ONLY,
                        ):
                            allowed_names.add(param.name)
                    return {
                        key: value
                        for key, value in candidate_kwargs.items()
                        if key in allowed_names
                    }

                # 检查是否是 async 函数
                is_async = self._is_async.get(name, inspect.iscoroutinefunction(tool))

                if is_async:
                    # 异步函数，用 asyncio 运行
                    if params and params[0] == 'args':
                        # 函数签名: (args, **kwargs) - 直接传递字典
                        filtered_kwargs = _filter_kwargs(kwargs, skip_first_arg=True)
                        result = asyncio.run(tool(args, **filtered_kwargs))
                    else:
                        # 函数签名: (command=..., ...) - 作为关键字参数传递
                        merged_args = {**args, **kwargs}
                        filtered_args = _filter_kwargs(merged_args)
                        result = asyncio.run(tool(**filtered_args))
                else:
                    # 同步函数
                    if params and params[0] == 'args':
                        # 函数签名: (args, **kwargs) - 直接传递字典
                        filtered_kwargs = _filter_kwargs(kwargs, skip_first_arg=True)
                        result = tool(args, **filtered_kwargs)
                    else:
                        # 函数签名: (command=..., ...) - 作为关键字参数传递
                        merged_args = {**args, **kwargs}
                        filtered_args = _filter_kwargs(merged_args)
                        result = tool(**filtered_args)

                if result is None:
                    return json.dumps({"success": True, "result": "completed"})
                return result
            elif isinstance(tool, dict):
                return json.dumps(tool)
            else:
                return tool_error(f"Tool '{name}' is not callable")
        except Exception as e:
            if raise_exceptions:
                raise
            return tool_error(f"Error executing tool '{name}': {str(e)}")
    
    def get_all_tool_names(self) -> List[str]:
        """获取所有工具名称"""
        return self.list_tools()
    
    def get_available_toolsets(self) -> Dict[str, dict]:
        """获取可用的工具集"""
        result = {}
        for toolset_name, tools in self._toolsets.items():
            result[toolset_name] = {
                "description": f"{toolset_name} tools",
                "tools": tools
            }
        return result
    
    def get_schema(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """获取工具的schema定义"""
        if tool_name in self._schemas:
            return self._schemas[tool_name]
        
        tool = self.get(tool_name)
        if tool is None:
            return None
        
        if isinstance(tool, dict):
            return tool.get("schema")
        return None


# 全局注册表实例
registry = ToolRegistry()
