"""Create the S3 native Superset assets through its versioned REST API.

Names are scoped to this experiment. Existing assets are reused, not overwritten.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.parse import quote_plus

import requests

from source_db import ROOT, check, credentials

BASE = "http://" + credentials().get("SUPERSET_BIND_HOST", "192.168.2.46") + ":8088"
RANGE = "2026-10-01 : 2026-11-01"


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    if os.geteuid() == 0 and "SUDO_UID" in os.environ:
        os.chown(path, int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"]))


class API:
    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["Referer"] = BASE + "/"
        login = self.call("POST", "/api/v1/security/login", {
            "username": "admin", "password": credentials()["SUPERSET_ADMIN_PASSWORD"],
            "provider": "db", "refresh": True,
        })
        self.session.headers["Authorization"] = "Bearer " + login["access_token"]
        self.session.headers["X-CSRFToken"] = self.call("GET", "/api/v1/security/csrf_token/")["result"]

    def call(self, method, path, data=None):
        result = self.session.request(method, BASE + path, json=data, timeout=120)
        if not result.ok:
            raise RuntimeError(f"{method} {path}: {result.status_code} {result.text[:2000]}")
        return result.json()

    def find(self, resource, field, value):
        result = self.call("GET", f"/api/v1/{resource}/?q=(page_size:100)")
        matches = [item for item in result["result"] if item.get(field) == value]
        check(len(matches) <= 1, f"Duplicate experiment {resource}: {value}")
        return matches[0]["id"] if matches else None


def query_context(dataset_id, trend=False, time_range=RANGE):
    query = {
        "time_range": time_range, "granularity": "order_date",
        "filters": [], "extras": {"having": "", "where": ""},
        "applied_time_extras": {}, "columns": [], "metrics": ["net_revenue"],
        "orderby": [], "annotation_layers": [], "row_limit": 1000,
        "series_limit": 0, "order_desc": False,
        "url_params": {}, "custom_params": {}, "custom_form_data": {},
    }
    if trend:
        query["columns"] = [{
            "timeGrain": "P1D", "columnType": "BASE_AXIS", "sqlExpression": "order_date",
            "label": "order_date", "expressionType": "SQL",
        }]
    return {"datasource": {"id": dataset_id, "type": "table"}, "force": True,
            "queries": [query], "result_format": "json", "result_type": "full"}


def main():
    api = API()
    database_id = api.find("database", "database_name", "008 MySQL sales (read-only)")
    if database_id is None:
        password = quote_plus(credentials()["MYSQL_SOURCE_READER_PASSWORD"])
        database_id = api.call("POST", "/api/v1/database/", {
            "database_name": "008 MySQL sales (read-only)",
            "sqlalchemy_uri": f"mysql+pymysql://bi_reader:{password}@mysql-source:3306/sales?charset=utf8mb4",
            "expose_in_sqllab": True, "allow_dml": False,
            "allow_ctas": False, "allow_cvas": False,
            "extra": json.dumps({"metadata_params": {}, "engine_params": {}}),
        })["id"]
    dataset_id = api.find("dataset", "table_name", "008_order_net_v1")
    sql = (ROOT / "sql/mysql/order_net.sql").read_text().strip().rstrip(";")
    if dataset_id is None:
        dataset_id = api.call("POST", "/api/v1/dataset/", {
            "database": database_id, "schema": "sales", "table_name": "008_order_net_v1", "sql": sql,
        })["id"]
    dataset = api.call("GET", f"/api/v1/dataset/{dataset_id}")["result"]
    check(dataset.get("sql", "").strip().rstrip(";") == sql, "Dataset SQL changed; review before replacing")
    if not any(m["metric_name"] == "net_revenue" for m in dataset.get("metrics", [])):
        columns = [{"id": c["id"], "column_name": c["column_name"], "is_dttm": c["column_name"] == "order_date",
                    "type": c["type"], "groupby": True, "filterable": True} for c in dataset["columns"]]
        api.call("PUT", f"/api/v1/dataset/{dataset_id}", {
            "main_dttm_col": "order_date", "columns": columns,
            "description": "一行一订单。净收入=实付-已记录退款，人民币元，退款归属原订单日。仅合成数据。",
            "metrics": [{"metric_name": "net_revenue", "expression": "SUM(net_amount)",
                         "verbose_name": "净收入", "d3format": ",.2f"}],
        })
    dashboard_id = api.find("dashboard", "dashboard_title", "008 净收入入门看板")
    if dashboard_id is None:
        dashboard_id = api.call("POST", "/api/v1/dashboard/", {
            "dashboard_title": "008 净收入入门看板", "slug": "bi008-net-revenue", "published": True,
        })["id"]
    common = {
        "datasource": f"{dataset_id}__table", "time_range": RANGE,
        "granularity_sqla": "order_date", "time_grain_sqla": "P1D", "adhoc_filters": [],
        "row_limit": 1000,
    }
    specs = [
        ("008 净收入", "big_number_total", {**common, "viz_type": "big_number_total",
          "metric": "net_revenue", "y_axis_format": ",.2f", "subheader": "人民币元 · 退款归属订单日"}, False),
        ("008 按日净收入", "echarts_timeseries_line", {**common, "viz_type": "echarts_timeseries_line",
          "metrics": ["net_revenue"], "x_axis": "order_date", "x_axis_sort_asc": True,
          "groupby": [], "y_axis_format": ",.2f", "show_legend": False, "rich_tooltip": True,
          "tooltipTimeFormat": "smart_date", "seriesType": "line"}, True),
    ]
    chart_ids = []
    for name, viz, params, trend in specs:
        chart_id = api.find("chart", "slice_name", name)
        if chart_id is None:
            chart_id = api.call("POST", "/api/v1/chart/", {
                "slice_name": name, "viz_type": viz, "datasource_id": dataset_id,
                "datasource_type": "table", "params": json.dumps(params, ensure_ascii=False),
                "query_context": json.dumps(query_context(dataset_id, trend)), "dashboards": [dashboard_id],
            })["id"]
        chart_ids.append(chart_id)
    dashboard = api.call("GET", f"/api/v1/dashboard/{dashboard_id}")["result"]
    if not dashboard.get("position_json") or dashboard["position_json"] in ("{}", "null"):
        position = {
            "DASHBOARD_VERSION_KEY": "v2",
            "ROOT_ID": {"id": "ROOT_ID", "type": "ROOT", "children": ["GRID_ID"]},
            "GRID_ID": {"id": "GRID_ID", "type": "GRID", "children": ["ROW-008"], "parents": ["ROOT_ID"]},
            "HEADER_ID": {"id": "HEADER_ID", "type": "HEADER", "meta": {"text": "008 净收入入门看板"}},
            "ROW-008": {"id": "ROW-008", "type": "ROW", "parents": ["ROOT_ID", "GRID_ID"],
                        "children": ["CHART-008-KPI", "CHART-008-TREND"], "meta": {"background": "BACKGROUND_TRANSPARENT"}},
        }
        for key, cid, name, width in zip(["CHART-008-KPI", "CHART-008-TREND"], chart_ids,
                                        ["008 净收入", "008 按日净收入"], [4, 8]):
            position[key] = {"id": key, "type": "CHART", "children": [],
                             "parents": ["ROOT_ID", "GRID_ID", "ROW-008"],
                             "meta": {"chartId": cid, "sliceName": name, "width": width, "height": 50}}
        metadata = {"native_filter_configuration": [{
            "id": "NATIVE_FILTER-008-DATE", "type": "NATIVE_FILTER", "name": "订单日期",
            "filterType": "filter_time", "targets": [{}],
            "controlValues": {"enableEmptyFilter": False},
            "defaultDataMask": {"extraFormData": {"time_range": RANGE}, "filterState": {"value": RANGE}},
            "cascadeParentIds": [], "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
        }], "timed_refresh_immune_slices": [], "refresh_frequency": 0,
            "chart_configuration": {}, "global_chart_configuration": {"scope": {"rootPath": ["ROOT_ID"], "excluded": []}}}
        api.call("PUT", f"/api/v1/dashboard/{dashboard_id}", {
            "position_json": json.dumps(position, ensure_ascii=False), "json_metadata": json.dumps(metadata, ensure_ascii=False),
        })
    full = api.call("POST", "/api/v1/chart/data", query_context(dataset_id))
    day = api.call("POST", "/api/v1/chart/data", query_context(dataset_id, time_range="2026-10-01 : 2026-10-02"))
    check(float(full["result"][0]["data"][0]["net_revenue"]) == 245, "Full-month API query differs from baseline")
    check(float(day["result"][0]["data"][0]["net_revenue"]) == 120, "One-day API query differs from baseline")
    report = {"verified_at_utc": datetime.now(timezone.utc).isoformat(), "database_id": database_id,
              "dataset_id": dataset_id, "chart_ids": chart_ids, "dashboard_id": dashboard_id,
              "dashboard_url": BASE + f"/superset/dashboard/{dashboard_id}/",
              "api_net": 245, "api_one_day_net": 120,
              "creation_method": "Superset native REST API; browser interaction verified separately"}
    save_json(ROOT / "evidence/s3-assets.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
