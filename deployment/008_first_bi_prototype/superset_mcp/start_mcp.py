"""S7 fixed-version compatibility: preserve existing params for same-type updates.

The official save path replaces params, unlike its preview merge path. Patch only
that payload builder in this process. Native tool/auth/command code remains in use.
"""
import hashlib
from datetime import date
from importlib import import_module
from importlib.metadata import version
import json
import logging
from pathlib import Path

UPSTREAM_SHA256='6d2e8e5431b755c0f589e380953764343e338468d037d25c8734195524512b49'
CHART_UTILS_SHA256='e5be11b828d5c62faa781fd8664e9ee51ce8053f6781cd03e6b810a9776faac8'


def install_date_compatibility():
    """Translate validated business-date bounds into native temporal filters.

    Only the audited all-date dataset 2/order_date mapping is enabled. No SQL
    strings from the client are accepted. Original October fixtures stay intact.
    """
    utils=import_module('superset.mcp_service.chart.chart_utils')
    if hashlib.sha256(Path(utils.__file__).read_bytes()).hexdigest()!=CHART_UTILS_SHA256:
        raise RuntimeError('BI008_DATE_COMPAT: chart mapper changed; re-audit before starting')
    original=utils.map_config_to_form_data
    def temporal_form_data(config,dataset_id=None):
        form=original(config,dataset_id=dataset_id)
        if str(dataset_id)!='2':return form
        bounds=[f for f in (config.filters or []) if f.column=='order_date']
        if not bounds:return form
        if len(bounds)!=2 or sorted(f.op for f in bounds)!=['<','>=']:
            raise ValueError('BI008_DATE_RANGE: order_date requires exactly >= start and < end')
        values={f.op:f.value for f in bounds}
        for value in values.values():
            if not isinstance(value,str) or len(value)!=10 or date.fromisoformat(value).isoformat()!=value:
                raise ValueError('BI008_DATE_RANGE: dates must be valid YYYY-MM-DD')
        if values['>=']>=values['<']:
            raise ValueError('BI008_DATE_RANGE: start must precede exclusive end')
        interval=values['>=']+' : '+values['<']
        form['adhoc_filters']=[f for f in form.get('adhoc_filters',[]) if f.get('subject')!='order_date']
        form['adhoc_filters'].append({'clause':'WHERE','expressionType':'SIMPLE','subject':'order_date',
                                     'operator':'TEMPORAL_RANGE','comparator':interval})
        # Native get_chart_data's cached-preview path reads QueryObject filters,
        # but does not translate adhoc_filters or copy time_range/granularity.
        form['filters']=utils.adhoc_filters_to_query_filters(form['adhoc_filters'])
        form['time_range']=interval
        form['granularity_sqla']='order_date'
        return form
    utils.map_config_to_form_data=temporal_form_data
    # Native tools import the function by name; replace their module references too.
    for name in ['generate_chart','update_chart','update_chart_preview']:
        module=import_module('superset.mcp_service.chart.tool.'+name)
        if getattr(module,'map_config_to_form_data',None) is original:
            module.map_config_to_form_data=temporal_form_data
    logging.warning('BI008_DATE_COMPAT enabled: dataset 2 order_date bounds become native TEMPORAL_RANGE')


def install_compatibility():
    if version('apache-superset')!='6.1.0' or version('fastmcp')!='3.1.0':
        raise RuntimeError('BI008_CHART_COMPAT: unverified Superset/FastMCP version')
    module=import_module('superset.mcp_service.chart.tool.update_chart')
    if hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()!=UPSTREAM_SHA256:
        raise RuntimeError('BI008_CHART_COMPAT: upstream update source changed; re-audit before starting')
    original=module._build_update_payload
    def preserving_payload(request,chart):
        payload=original(request,chart)
        if isinstance(payload,dict) and 'params' in payload:
            if payload['viz_type']!=chart.viz_type:
                raise ValueError('BI008_CHART_COMPAT: cross-type replacement requires separate review; only same-type preservation verified')
            existing=json.loads(chart.params or '{}')
            updated=json.loads(payload['params'])
            if not isinstance(existing,dict) or not isinstance(updated,dict):
                raise ValueError('BI008_CHART_COMPAT: invalid saved params; refusing replacement')
            payload['params']=json.dumps({**existing,**updated})
        return payload
    module._build_update_payload=preserving_payload
    install_date_compatibility()
    logging.warning('BI008_S7_COMPAT enabled: preserve unmapped same-type chart parameters; upstream SHA verified')


def main():
    from superset.mcp_service import server
    native_init=server.init_fastmcp_server
    def initialize(*args,**kwargs):
        mcp=native_init(*args,**kwargs)
        install_compatibility()
        return mcp
    server.init_fastmcp_server=initialize
    server.run_server(host='0.0.0.0',port=5008)


if __name__=='__main__':main()
