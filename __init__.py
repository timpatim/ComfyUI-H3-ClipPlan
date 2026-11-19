"""H3 Clip Plan: plan-driven MiniMax H3 music-video nodes for ComfyUI."""
try:
    import folder_paths as _folder_paths  # noqa: F401
except ModuleNotFoundError:  # running outside ComfyUI (tests, plan_tool.py)
    NODE_CLASS_MAPPINGS = {}
    NODE_DISPLAY_NAME_MAPPINGS = {}
else:
    from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
