"""Start one verified W3 release; refuse old-session queries after pointer changes."""
import os
from guarded_wren_mcp import make_server
from w3_release import resolve


def make_release_server():
    project, descriptor, pointer = resolve()
    os.environ['WREN_HOME'] = str(project/'runtime')
    os.environ['WREN_PROJECT_HOME'] = str(project)
    os.environ['WREN_MEMORY_BACKEND'] = 'grep'
    server = make_server(project)
    def current():
        return resolve()[2] == pointer
    for name in ['run_sql','dry_run','dry_plan','query_cube']:
        tool = server._tool_manager.get_tool(name)
        def guarded(*args, _native=tool.fn, **kwargs):
            if not current():
                raise ValueError('BI008_RELEASE_REVISION: active release changed; restart client before querying')
            return _native(*args, **kwargs)
        tool.fn = guarded
    tool = server._tool_manager.get_tool('get_runtime_contract')
    native = tool.fn
    def contract():
        result = native()
        result.update({'project':'bi008_w3_fixture','release_id':descriptor['release_id'],'metric':descriptor['metric'],
                       'active_release_matches_session':current(),'scope':'W3 synthetic fixture, not S2 baseline',
                       'publication_review':descriptor['review']})
        return result
    tool.fn = contract
    return server


if __name__ == '__main__':make_release_server().run(transport='stdio')
