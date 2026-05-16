__all__ = ["query_knowledge_vault_tool", "save_to_knowledge_vault_tool"]

def __getattr__(name: str):
    if name == "query_knowledge_vault_tool":
        from .tool import query_knowledge_vault_tool

        return query_knowledge_vault_tool
    if name == "save_to_knowledge_vault_tool":
        from .save_tool import save_to_knowledge_vault_tool

        return save_to_knowledge_vault_tool
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
