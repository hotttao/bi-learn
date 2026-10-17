"""Publish the checked S4 SQL as native Superset assets without replacing S3 charts."""
import copy
from datetime import datetime, timezone
import hashlib
import json

from source_db import ROOT, check
from s3_assets import API, BASE, RANGE, query_context, save_json
from verify_s4 import SQL, amounts, summary, validate_customer_keys, trino, quality


def main():
    # Fail before publishing if the sources or semantic SQL no longer meet the contract.
    validate_customer_keys(trino('SELECT dept_id,customer_id,industry FROM crm.public.customers')['rows'])
    check(amounts(summary()) == {'软件': '120.00', '零售': '125.00'}, 'S4 baseline changed; review before publishing')
    check(trino(f'SELECT COUNT(*), COUNT(DISTINCT order_id) FROM ({SQL}) x')['rows'] == [[4, 4]], 'Order grain differs')
    contract = json.loads((ROOT / 'semantic/net_revenue.v1.json').read_text())
    check(contract['expression'] == 'SUM(net_amount)' and contract['version'] == 1, 'Contract mismatch')
    quality_result = quality()
    api = API()
    dbid = api.find('database', 'database_name', '008 Trino federation (source readers)')
    if dbid is None:
        dbid = api.call('POST', '/api/v1/database/', {
            'database_name': '008 Trino federation (source readers)',
            'sqlalchemy_uri': 'trino://bi008_superset@trino:8080/mysql/sales',
            'expose_in_sqllab': True, 'allow_dml': False, 'allow_ctas': False, 'allow_cvas': False,
            'extra': json.dumps({'metadata_params': {}, 'engine_params': {}}),
        })['id']
    dsid = api.find('dataset', 'table_name', '008_orders_enriched_v1')
    if dsid is None:
        dsid = api.call('POST', '/api/v1/dataset/', {'database': dbid, 'schema': 'sales',
                        'table_name': '008_orders_enriched_v1', 'sql': SQL})['id']
    ds = api.call('GET', f'/api/v1/dataset/{dsid}')['result']
    check(ds.get('sql', '').strip().rstrip(';') == SQL, 'Dataset SQL changed; do not overwrite')
    if not any(m['metric_name'] == 'net_revenue' for m in ds.get('metrics', [])):
        api.call('PUT', f'/api/v1/dataset/{dsid}', {
            'main_dttm_col': 'order_date',
            'columns': [{'id': c['id'], 'column_name': c['column_name'], 'type': c['type'],
                         'is_dttm': c['column_name'] == 'order_date', 'groupby': True, 'filterable': True} for c in ds['columns']],
            'description': 'net_revenue@1；一行一订单；退款先汇总；客户关联含部门；当前行业，无匹配为未知行业；CNY元。S4未实现部门隔离。',
            'metrics': [{'metric_name': 'net_revenue', 'expression': contract['expression'], 'verbose_name': '净收入', 'd3format': ',.2f'}],
        })
    dashboard_id = json.loads((ROOT / 'evidence/s3-assets.json').read_text())['dashboard_id']
    params = {'datasource': f'{dsid}__table', 'viz_type': 'echarts_timeseries_bar', 'metrics': ['net_revenue'],
              'x_axis': 'industry', 'groupby': [], 'time_range': RANGE, 'granularity_sqla': 'order_date',
              'adhoc_filters': [], 'row_limit': 1000, 'y_axis_format': ',.2f', 'show_legend': False,
              'rich_tooltip': True, 'orientation': 'vertical', 'x_axis_sort_asc': True}
    qc = query_context(dsid)
    qc['queries'][0]['columns'] = ['industry']
    chart_id = api.find('chart', 'slice_name', '008 按当前行业净收入')
    if chart_id is None:
        chart_id = api.call('POST', '/api/v1/chart/', {
            'slice_name': '008 按当前行业净收入', 'viz_type': 'echarts_timeseries_bar',
            'datasource_id': dsid, 'datasource_type': 'table', 'params': json.dumps(params, ensure_ascii=False),
            'query_context': json.dumps(qc), 'dashboards': [dashboard_id],
        })['id']
    dashboard = api.call('GET', f'/api/v1/dashboard/{dashboard_id}')['result']
    pos = json.loads(dashboard['position_json'])
    before = copy.deepcopy(pos)
    key, rowkey = 'CHART-008-INDUSTRY', 'ROW-008-INDUSTRY'
    if key not in pos:
        check(rowkey not in pos, 'Layout node collision')
        pos['GRID_ID']['children'].append(rowkey)
        pos[rowkey] = {'id': rowkey, 'type': 'ROW', 'parents': ['ROOT_ID', 'GRID_ID'],
                       'children': [key], 'meta': {'background': 'BACKGROUND_TRANSPARENT'}}
        pos[key] = {'id': key, 'type': 'CHART', 'children': [], 'parents': ['ROOT_ID', 'GRID_ID', rowkey],
                    'meta': {'chartId': chart_id, 'sliceName': '008 按当前行业净收入', 'width': 12, 'height': 50}}
        # Re-read before mutation; retain all existing nodes and metadata. No API CAS is assumed.
        check(api.call('GET', f'/api/v1/dashboard/{dashboard_id}')['result']['position_json'] == dashboard['position_json'],
              'Dashboard changed concurrently; stop for review')
        api.call('PUT', f'/api/v1/dashboard/{dashboard_id}', {'position_json': json.dumps(pos, ensure_ascii=False)})
    check(pos[key]['meta']['chartId'] == chart_id, 'Existing industry layout points to a different chart; review required')
    saved = json.loads(api.call('GET', f'/api/v1/dashboard/{dashboard_id}')['result']['position_json'])
    for node, value in before.items():
        if node != 'GRID_ID':
            check(saved[node] == value, f'Existing layout node changed: {node}')
    full = api.call('POST', '/api/v1/chart/data', qc)['result'][0]['data']
    check({r['industry']: r['net_revenue'] for r in full} == {'软件': 120, '零售': 125}, 'Industry API differs')
    day_qc = copy.deepcopy(qc)
    day_qc['queries'][0]['time_range'] = '2026-10-01 : 2026-10-02'
    day = api.call('POST', '/api/v1/chart/data', day_qc)['result'][0]['data']
    check(sum(r['net_revenue'] for r in day) == 120, 'Industry date filtering differs')
    report = {'verified_at_utc': datetime.now(timezone.utc).isoformat(), 'database_id': dbid,
              'dataset_id': dsid, 'chart_id': chart_id, 'dashboard_id': dashboard_id,
              'dashboard_url': BASE + f'/superset/dashboard/{dashboard_id}/', 'contract': 'net_revenue@1',
              'sql_sha256': hashlib.sha256(SQL.encode()).hexdigest(), 'industry_api': full, 'one_day_api': day,
              'quality': quality_result, 'existing_layout_preserved': True, 's3_charts': 'unchanged: MySQL baseline; industry chart uses Trino'}
    save_json(ROOT / 'evidence/s4-assets.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
