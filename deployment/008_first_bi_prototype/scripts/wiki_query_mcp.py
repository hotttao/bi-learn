"""Existing Wren query tools only; knowledge is read from a published file Wiki."""
from functools import wraps
from guarded_wren_mcp import make_server
from prepare_s9_wiki import PROJECT,verify_release


def server():
    verify_release();mcp=make_server(PROJECT);manager=mcp._tool_manager
    for name in list(manager._tools):
        if name not in ['dry_plan','dry_run','run_sql']:manager._tools.pop(name)
    for tool in manager._tools.values():
        def bound(original):
            @wraps(original)
            def call(*args,**kwargs):
                verify_release()
                return original(*args,**kwargs)
            return call
        tool.fn=bound(tool.fn)
    return mcp


if __name__=='__main__':server().run(transport='stdio')
