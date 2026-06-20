"""Hindsight Memory Provider - Structured knowledge graph memory system.

Hindsight is a memory architecture that organizes memory into structured
networks distinguishing world facts, agent experiences, entity summaries,
and evolving beliefs. It supports retain, recall, and reflect operations.

For more information: https://hindsight.vectorize.io
"""

from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider


class HindsightMemoryProvider(MemoryProvider):
    """Hindsight memory provider implementation."""

    @property
    def name(self) -> str:
        return "hindsight"

    def is_available(self) -> bool:
        """Check if Hindsight is available."""
        try:
            import hindsight
            return True
        except ImportError:
            return False

    def initialize(self, session_id: str, **kwargs) -> None:
        """Initialize Hindsight provider."""
        try:
            from hindsight import HindsightClient
            self._client = HindsightClient()
            self._session_id = session_id
        except ImportError:
            raise RuntimeError("Hindsight not installed. Install with: pip install hindsight-client")

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Return Hindsight tool schemas."""
        return [
            {
                "name": "hindsight_recall",
                "description": "从 Hindsight 记忆系统中检索相关记忆。支持按实体、主题或时间范围查询。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "检索查询词"
                        },
                        "entity": {
                            "type": "string",
                            "description": "可选，按实体过滤"
                        },
                        "max_results": {
                            "type": "integer",
                            "default": 10,
                            "minimum": 1,
                            "description": "返回的最大结果数量"
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "hindsight_retain",
                "description": "将信息保存到 Hindsight 记忆系统。支持保存事实、实体关系和经验。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "要保存的内容"
                        },
                        "entity": {
                            "type": "string",
                            "description": "可选，关联的实体名称"
                        },
                        "memory_type": {
                            "type": "string",
                            "enum": ["fact", "experience", "summary", "belief"],
                            "default": "fact",
                            "description": "记忆类型"
                        }
                    },
                    "required": ["content"]
                }
            },
            {
                "name": "hindsight_reflect",
                "description": "触发 Hindsight 的反思操作，综合跨记忆的洞察并更新信念网络。",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            }
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        """Handle Hindsight tool calls."""
        import json
        
        if not hasattr(self, '_client'):
            return json.dumps({"success": False, "error": "Hindsight not initialized"})
        
        try:
            if tool_name == "hindsight_recall":
                query = args.get("query", "")
                entity = args.get("entity")
                max_results = args.get("max_results", 10)
                
                results = self._client.recall(
                    query=query,
                    entity=entity,
                    limit=max_results
                )
                return json.dumps({"success": True, "data": results})
            
            elif tool_name == "hindsight_retain":
                content = args.get("content", "")
                entity = args.get("entity")
                memory_type = args.get("memory_type", "fact")
                
                result = self._client.retain(
                    content=content,
                    entity=entity,
                    memory_type=memory_type
                )
                return json.dumps({"success": True, "data": result})
            
            elif tool_name == "hindsight_reflect":
                result = self._client.reflect()
                return json.dumps({"success": True, "data": result})
            
            else:
                return json.dumps({"success": False, "error": f"Unknown tool: {tool_name}"})
        
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Sync conversation turn to Hindsight."""
        if hasattr(self, '_client'):
            try:
                self._client.retain(
                    content=f"User: {user_content}\nAssistant: {assistant_content}",
                    memory_type="experience"
                )
            except Exception:
                pass

    def shutdown(self) -> None:
        """Clean shutdown."""
        if hasattr(self, '_client'):
            del self._client
