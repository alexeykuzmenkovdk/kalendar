#!/usr/bin/env python3
"""Веб-планер заходов морских судов без внешних зависимостей."""

from __future__ import annotations

import argparse
import csv
import html
import json
import io
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

DATE_FMT = "%Y-%m-%d"
DEFAULT_DB = Path("planner_db.json")
PLAN_HORIZON_DAYS = 365
PLACEHOLDER = "¤"

DEFAULT_SHIPS = [
    "т/х «Анатолий Иванов»",
    "т/х «Ерофей Хабаров»",
    "т/х «Русский Восток»",
    "т/х «Механик Красковский»",
]

DEFAULT_PORTS = [
    "Владивосток",
    "Крабозаводск (о. Шикотан)",
    "Малокурильское (о. Шикотан)",
    "Южно-Курильск (о. Кунашир)",
    "Курильск (о. Итуруп)",
    "Корсаков (о. Сахалин)",
    "Подъяпольского",
    "Северо-Курильск",
    "Невельск",
    "Славянка",
]


@dataclass
class Stop:
    port: str
    arrival: str
    departure: str
    skipped: bool = False


@dataclass
class Plan:
    id: int
    ship: str
    route: List[str]
    start_date: str
    end_date: str
    stops: List[Stop]
    frozen_rows: List[int] | None = None


class PlannerDB:
    def __init__(self, path: Path = DEFAULT_DB) -> None:
        self.path = path
        self.data = self._load_or_create()

    def _load_or_create(self) -> Dict:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))

        transition_days = {
            from_port: {to_port: (0 if from_port == to_port else 2) for to_port in DEFAULT_PORTS}
            for from_port in DEFAULT_PORTS
        }
        stay_days = {port: 1 for port in DEFAULT_PORTS}

        data = {
            "ships": DEFAULT_SHIPS,
            "ports": DEFAULT_PORTS,
            "transition_days": transition_days,
            "stay_days": stay_days,
            "plans": [],
            "next_plan_id": 1,
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")


class PlannerService:
    def __init__(self, db: PlannerDB) -> None:
        self.db = db

    @staticmethod
    def _parse_date(value: str) -> datetime:
        return datetime.strptime(value, DATE_FMT)

    @staticmethod
    def _fmt_date(value: datetime) -> str:
        return value.strftime(DATE_FMT)

    def list_ships(self) -> List[str]:
        return self.db.data["ships"]

    def list_ports(self) -> List[str]:
        return self.db.data["ports"]

    def list_plans(self) -> List[Dict]:
        return self.db.data["plans"]

    def get_transition_days(self) -> Dict[str, Dict[str, int]]:
        return self.db.data["transition_days"]

    def get_stay_days(self) -> Dict[str, int]:
        return self.db.data["stay_days"]

    def create_plan(self, ship: str, route: List[str], start_date: str) -> Dict:
        if ship not in self.db.data["ships"]:
            raise ValueError(f"Судно не найдено: {ship}")
        if len(route) < 2:
            raise ValueError("В маршруте должно быть минимум 2 порта")
        for port in route:
            if port not in self.db.data["ports"]:
                raise ValueError(f"Неизвестный порт: {port}")

        start = self._parse_date(start_date)
        limit = start + timedelta(days=PLAN_HORIZON_DAYS)
        stops: List[Stop] = []

        current_departure = start
        idx = 0
        while current_departure < limit:
            port = route[idx % len(route)]
            if not stops:
                arrival = current_departure
            else:
                prev_port = stops[-1].port
                travel_days = self.db.data["transition_days"][prev_port][port]
                arrival = current_departure + timedelta(days=travel_days)

            stay = self.db.data["stay_days"].get(port, 1)
            departure = arrival + timedelta(days=stay)
            stops.append(Stop(port=port, arrival=self._fmt_date(arrival), departure=self._fmt_date(departure), skipped=False))

            current_departure = departure
            idx += 1

        plan_id = self.db.data["next_plan_id"]
        self.db.data["next_plan_id"] += 1

        plan = Plan(
            id=plan_id,
            ship=ship,
            route=route,
            start_date=start_date,
            end_date=self._fmt_date(limit),
            stops=stops,
            frozen_rows=[],
        )
        result = asdict(plan)
        self.db.data["plans"].append(result)
        self.db.save()
        return result

    def get_plan(self, plan_id: int) -> Dict:
        for plan in self.db.data["plans"]:
            if plan["id"] == plan_id:
                if "frozen_rows" not in plan:
                    plan["frozen_rows"] = []
                plan.pop("frozen_from", None)
                plan.pop("frozen_to", None)
                plan.pop("frozen_until", None)
                return plan
        raise ValueError(f"План #{plan_id} не найден")

    def delete_plan(self, plan_id: int) -> None:
        plans = self.db.data["plans"]
        for idx, plan in enumerate(plans):
            if plan["id"] == plan_id:
                del plans[idx]
                self.db.save()
                return
        raise ValueError(f"План #{plan_id} не найден")

    def clear_plan_schedule(self, plan_id: int) -> Dict:
        plan = self.get_plan(plan_id)
        for stop in plan["stops"]:
            stop["arrival"] = ""
            stop["departure"] = ""
            stop["skipped"] = False
        plan["frozen_rows"] = []
        self.db.save()
        return plan

    def _apply_shift(self, plan: Dict, start_index: int, shift_days: int) -> None:
        for idx in range(start_index, len(plan["stops"])):
            old_arrival = self._parse_date(plan["stops"][idx]["arrival"])
            old_departure = self._parse_date(plan["stops"][idx]["departure"])
            plan["stops"][idx]["arrival"] = self._fmt_date(old_arrival + timedelta(days=shift_days))
            plan["stops"][idx]["departure"] = self._fmt_date(old_departure + timedelta(days=shift_days))

    def manual_update_stop(
        self,
        plan_id: int,
        stop_index: int,
        arrival: Optional[str] = None,
        departure: Optional[str] = None,
        propagate: bool = True,
    ) -> Dict:
        plan = self.get_plan(plan_id)
        if stop_index < 0 or stop_index >= len(plan["stops"]):
            raise ValueError("Неверный индекс остановки")

        stop = plan["stops"][stop_index]
        old_arrival = self._parse_date(stop["arrival"])
        old_departure = self._parse_date(stop["departure"])

        if arrival:
            self._parse_date(arrival)
            stop["arrival"] = arrival
        if departure:
            self._parse_date(departure)
            stop["departure"] = departure

        new_arrival = self._parse_date(stop["arrival"])
        new_departure = self._parse_date(stop["departure"])
        if new_departure < new_arrival:
            raise ValueError("Дата отхода не может быть раньше даты прихода")

        if propagate:
            shift = (new_departure - old_departure).days
            if shift == 0:
                shift = (new_arrival - old_arrival).days
            if shift != 0:
                self._apply_shift(plan, stop_index + 1, shift)

        self.db.save()
        return plan

    def update_plan_from_manual_table(self, plan_id: int, manual_map: Dict[int, Tuple[str, str]]) -> Dict:
        plan = self.get_plan(plan_id)
        locked_indices = set()
        route_len = len(plan["route"])
        frozen_rows = set(plan.get("frozen_rows", []))
        frozen_indices = set()
        for idx, stop in enumerate(plan["stops"]):
            row_idx = idx // route_len
            if row_idx not in frozen_rows:
                continue
            if (stop.get("arrival") and stop.get("departure")) or stop.get("skipped"):
                frozen_indices.add(idx)

        for idx, stop in enumerate(plan["stops"]):
            stop.setdefault("skipped", False)
            if idx not in manual_map:
                continue
            if idx in frozen_indices:
                continue

            arrival, departure = manual_map[idx]
            old_arrival = stop.get("arrival", "")
            old_departure = stop.get("departure", "")
            if not arrival and not departure:
                if old_arrival or old_departure:
                    stop["arrival"] = ""
                    stop["departure"] = ""
                    stop["skipped"] = True
                else:
                    stop["skipped"] = False
                continue

            if not arrival or not departure:
                raise ValueError("Для ручного ввода укажите обе даты или очистите обе для пропуска порта")

            self._parse_date(arrival)
            self._parse_date(departure)
            if self._parse_date(departure) < self._parse_date(arrival):
                raise ValueError("Дата отхода не может быть раньше даты прихода")

            stop["arrival"] = arrival
            stop["departure"] = departure
            stop["skipped"] = False
            if arrival != old_arrival or departure != old_departure:
                locked_indices.add(idx)

        prev_port = None
        current_departure = self._parse_date(plan["start_date"])

        for idx in range(len(plan["stops"])):
            stop = plan["stops"][idx]
            if stop.get("skipped"):
                continue

            if idx in frozen_indices:
                prev_port = stop["port"]
                current_departure = self._parse_date(stop["departure"])
                continue

            if idx in locked_indices:
                arrival = self._parse_date(stop["arrival"])
                departure = self._parse_date(stop["departure"])
            else:
                if prev_port is None:
                    arrival = current_departure
                else:
                    travel_days = self.db.data["transition_days"][prev_port][stop["port"]]
                    arrival = current_departure + timedelta(days=travel_days)

                stay_days = self.db.data["stay_days"].get(stop["port"], 1)
                departure = arrival + timedelta(days=stay_days)
                stop["arrival"] = self._fmt_date(arrival)
                stop["departure"] = self._fmt_date(departure)

            prev_port = stop["port"]
            current_departure = departure

        self.db.save()
        return self.get_plan(plan_id)

    def set_frozen_rows(self, plan_id: int, frozen_rows: List[int]) -> Dict:
        plan = self.get_plan(plan_id)
        if not frozen_rows:
            plan["frozen_rows"] = []
            self.db.save()
            return plan

        route_len = len(plan["route"])
        max_row = (len(plan["stops"]) - 1) // route_len
        normalized_rows = []
        for row in sorted(set(frozen_rows)):
            if row < 0 or row > max_row:
                raise ValueError("Некорректный индекс периода заморозки")
            normalized_rows.append(row)

        plan["frozen_rows"] = normalized_rows
        self.db.save()
        return plan

    def export_plan_csv(self, plan_id: int) -> str:
        plan = self.get_plan(plan_id)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["plan_id", "ship", "port", "arrival", "departure", "skipped"])
        for stop in plan["stops"]:
            writer.writerow(
                [
                    plan["id"],
                    plan["ship"],
                    stop["port"],
                    stop.get("arrival", ""),
                    stop.get("departure", ""),
                    "yes" if stop.get("skipped") else "no",
                ]
            )
        return buffer.getvalue()

    def export_plan_html(self, plan_id: int) -> str:
        table = self.build_schedule_table(plan_id)
        plan = table["plan"]
        header_ports = "".join(f"<th colspan='2'>{html.escape(port)}</th>" for port in table["ports"])
        sub_headers = "".join("<th>Приход</th><th>Отход</th>" for _ in table["ports"])

        rows = []
        for row in table["rows"]:
            cells = []
            for port in table["ports"]:
                cell = row[port]
                arrival = cell["arrival"]
                departure = cell["departure"]
                if arrival == table["placeholder"] and departure == table["placeholder"]:
                    cells.append(
                        f"<td><span class='placeholder'>{html.escape(table['placeholder'])}</span></td>"
                        f"<td><span class='placeholder'>{html.escape(table['placeholder'])}</span></td>"
                    )
                else:
                    cells.append(f"<td>{html.escape(arrival)}</td><td>{html.escape(departure)}</td>")
            rows.append(f"<tr>{''.join(cells)}</tr>")

        return """<!doctype html>
<html lang='ru'>
<head>
<meta charset='utf-8'>
<title>Расписание судозаходов</title>
<style>
body { font-family: Arial, sans-serif; }
h1 { margin-bottom: 6px; }
p { margin-top: 0; color: #333; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #333; padding: 6px; text-align: center; }
th { background: #f3f4f6; }
.placeholder { color: #687082; font-weight: bold; }
</style>
</head>
<body>
""" + (
            f"<h1>{html.escape(plan['ship'])}</h1>"
            f"<p>Период: {plan['start_date']} — {plan['end_date']}</p>"
            "<table>"
            f"<thead><tr>{header_ports}</tr><tr>{sub_headers}</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody>"
            "</table>"
            "</body></html>"
        )

    def set_transition(self, from_port: str, to_port: str, days: int) -> None:
        if from_port not in self.db.data["ports"] or to_port not in self.db.data["ports"]:
            raise ValueError("Неизвестный порт")
        if days < 0:
            raise ValueError("Дни перехода не могут быть отрицательными")
        self.db.data["transition_days"][from_port][to_port] = days
        self.db.save()

    def set_stay(self, port: str, days: int) -> None:
        if port not in self.db.data["ports"]:
            raise ValueError("Неизвестный порт")
        if days < 0:
            raise ValueError("Время стоянки не может быть отрицательным")
        self.db.data["stay_days"][port] = days
        self.db.save()

    def build_schedule_table(self, plan_id: int, placeholder: str = PLACEHOLDER) -> Dict:
        plan = self.get_plan(plan_id)
        route = plan["route"]

        port_columns: List[str] = []
        for port in route:
            if port not in port_columns:
                port_columns.append(port)

        rows_count = (len(plan["stops"]) + len(route) - 1) // len(route)
        rows = []
        for _ in range(rows_count):
            row = {port: {"arrival": placeholder, "departure": placeholder, "stop_index": None} for port in port_columns}
            rows.append(row)

        for idx, stop in enumerate(plan["stops"]):
            row_idx = idx // len(route)
            port = stop["port"]
            if port in port_columns:
                rows[row_idx][port] = {
                    "arrival": stop["arrival"],
                    "departure": stop["departure"],
                    "stop_index": idx,
                }

        return {"plan": plan, "ports": port_columns, "rows": rows, "placeholder": placeholder}


def layout(title: str, body: str, flash: str = "") -> str:
    return f"""<!doctype html>
<html lang='ru'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>{html.escape(title)}</title>
<style>
body {{ margin:0; font-family: Arial, sans-serif; background:#f2f4f8; }}
header {{ background:#1f4d8f; color:#fff; padding:14px 20px; display:flex; justify-content:space-between; align-items:center; }}
header a {{ color:#fff; text-decoration:none; margin-left:14px; }}
main {{ padding:16px; }}
.card {{ background:#fff; border-radius:8px; padding:14px; margin-bottom:14px; box-shadow:0 1px 4px rgba(0,0,0,.08); }}
.row {{ display:flex; gap:10px; align-items:end; flex-wrap:wrap; }}
label {{ display:flex; flex-direction:column; gap:4px; font-size:13px; }}
input, select, button {{ padding:8px; border:1px solid #b8c2d1; border-radius:6px; font-size:14px; }}
button {{ cursor:pointer; }}
.primary {{ background:#1f4d8f; color:#fff; }}
.table-wrap {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; }}
th, td {{ border:1px solid #9aa5b1; padding:6px; text-align:center; }}
.flash {{ margin:10px 0; padding:10px; border-radius:6px; background:#d1fae5; }}
.err {{ background:#fee2e2; }}
.placeholder {{ color:#687082; font-weight:bold; }}
</style>
</head>
<body>
<header>
<div>Планер графика захода морских судов</div>
<nav><a href='/'>Главная</a><a href='/technical'>Техническая страница</a></nav>
</header>
<main>
{flash}
{body}
</main>
</body>
</html>"""


def render_index(service: PlannerService, selected_id: Optional[int], flash_text: str = "", is_error: bool = False) -> str:
    plans = service.list_plans()
    ships_options = "".join(f"<option>{html.escape(s)}</option>" for s in service.list_ships())
    plans_options = "".join(
        f"<option value='{p['id']}' {'selected' if selected_id == p['id'] else ''}>"
        f"#{p['id']} | {html.escape(p['ship'])} | {p['start_date']} — {p['end_date']}</option>"
        for p in plans
    )

    table_html = ""
    if selected_id is not None:
        table = service.build_schedule_table(selected_id)
        header_ports = "".join(f"<th colspan='2'>{html.escape(port)}</th>" for port in table["ports"])
        header_ports += "<th rowspan='2'>Заморозка</th>"
        sub_headers = "".join("<th>Приход</th><th>Отход</th>" for _ in table["ports"])

        route_len = len(table["plan"]["route"])
        frozen_rows = set(table["plan"].get("frozen_rows", []))
        frozen_indices = {
            idx
            for idx, stop in enumerate(table["plan"]["stops"])
            if idx // route_len in frozen_rows and stop.get("arrival") and stop.get("departure")
        }

        rows_html = []
        for row_idx, row in enumerate(table["rows"]):
            cells = []
            period_frozen = row_idx in frozen_rows
            for port in table["ports"]:
                cell = row[port]
                if cell["stop_index"] is not None:
                    is_frozen = cell["stop_index"] in frozen_indices
                    frozen_attrs = " readonly style='background:#eef3ff;' " if is_frozen else ""
                    frozen_cell_style = " style='background:#eef3ff;' " if is_frozen else ""
                    cells.append(
                        f"<td{frozen_cell_style}><input type='date' name='stop_{cell['stop_index']}_arrival' value='{cell['arrival']}'{frozen_attrs}></td>"
                    )
                    cells.append(
                        f"<td{frozen_cell_style}><input type='date' name='stop_{cell['stop_index']}_departure' value='{cell['departure']}'{frozen_attrs}></td>"
                    )
                else:
                    cells.append(f"<td><span class='placeholder'>{table['placeholder']}</span></td><td><span class='placeholder'>{table['placeholder']}</span></td>")
            freeze_cell = (
                f"<td style='background:#eef3ff;'><input type='checkbox' name='freeze_row_{row_idx}' checked></td>"
                if period_frozen
                else f"<td><input type='checkbox' name='freeze_row_{row_idx}'></td>"
            )
            rows_html.append(f"<tr>{''.join(cells)}{freeze_cell}</tr>")

        freeze_note = "Отмеченные периоды заморожены и не изменяются при обновлении расписания." if frozen_rows else ""

        table_html = f"""
<section class='card'>
<h2>{html.escape(table['plan']['ship'])} ({table['plan']['start_date']} — {table['plan']['end_date']})</h2>
<form method='post' action='/plans/{table['plan']['id']}/update'>
{f"<p style='margin:8px 0 0;color:#1f4d8f;font-size:13px;'>{html.escape(freeze_note)}</p>" if freeze_note else ""}
<div class='table-wrap'>
<table>
<thead><tr>{header_ports}</tr><tr>{sub_headers}</tr></thead>
<tbody>{''.join(rows_html)}</tbody>
</table>
</div>
<div class='row'>
<button class='primary' type='submit'>Обновить расписание</button>
<button type='submit' formaction='/plans/{table['plan']['id']}/clear' formmethod='post' onclick="return confirm('Очистить только даты в таблице выбранного плана?');">Очистить даты в расписании</button>
<a href='/plans/{table['plan']['id']}/export' style='text-decoration:none;'><button type='button'>Экспорт CSV</button></a>
<a href='/plans/{table['plan']['id']}/export-html' style='text-decoration:none;'><button type='button'>Экспорт HTML</button></a>
</div>
</form>
</section>"""

    port_options = "".join(f"<option value='{html.escape(port)}'>{html.escape(port)}</option>" for port in service.list_ports())

    body = f"""
<section class='card'>
<h2>Создать план</h2>
<form class='row' method='post' action='/plans'>
<label>Судно<select name='ship'>{ships_options}</select></label>
<label>Дата старта<input type='date' name='start_date' value='{datetime.now().strftime(DATE_FMT)}'></label>
<label style='min-width:260px;'>Порт<select id='next-port'>{port_options}</select></label>
<button type='button' id='add-port'>Добавить порт</button>
<label style='min-width:420px;flex:1;'>Маршрут
<input type='hidden' name='route' id='route-field'>
<input type='text' id='route-preview' readonly placeholder='Выберите порты последовательно'>
</label>
<button type='submit'>Создать</button>
</form>
</section>
<section class='card'>
<h2>Выбрать план</h2>
<form class='row' method='get' action='/'>
<label style='min-width:420px;flex:1;'>План<select name='plan_id'>{plans_options}</select></label>
<button type='submit'>Открыть</button>
</form>
<form class='row' method='post' action='/plans/delete' onsubmit="return confirm('Удалить выбранный план?');">
<label style='min-width:420px;flex:1;'>План<select name='plan_id'>{plans_options}</select></label>
<button type='submit'>Удалить план</button>
</form>
</section>
{table_html}
<script>
(() => {{
  const addBtn = document.getElementById('add-port');
  const portSelect = document.getElementById('next-port');
  const routeField = document.getElementById('route-field');
  const routePreview = document.getElementById('route-preview');
  if (!addBtn || !portSelect || !routeField || !routePreview) return;

  const current = [];
  const redraw = () => {{
    routeField.value = current.join('||');
    routePreview.value = current.join(' → ');
  }};

  addBtn.addEventListener('click', () => {{
    if (!portSelect.value) return;
    current.push(portSelect.value);
    redraw();
  }});

  routePreview.addEventListener('dblclick', () => {{
    current.pop();
    redraw();
  }});
}})();
</script>
"""

    flash = f"<div class='flash {'err' if is_error else ''}'>{html.escape(flash_text)}</div>" if flash_text else ""
    return layout("Главная", body, flash=flash)


def render_technical(service: PlannerService, flash_text: str = "", is_error: bool = False) -> str:
    ports = service.list_ports()
    trans = service.get_transition_days()
    stay = service.get_stay_days()

    header = "".join(f"<th>{html.escape(p)}</th>" for p in ports)
    rows = []
    for f_port in ports:
        cells = []
        for t_port in ports:
            val = trans[f_port][t_port]
            name = f"tr__{f_port}__{t_port}"
            cells.append(f"<td><input type='number' min='0' name='{html.escape(name)}' value='{val}'></td>")
        rows.append(f"<tr><th>{html.escape(f_port)}</th>{''.join(cells)}</tr>")

    stay_rows = "".join(
        f"<tr><td>{html.escape(p)}</td><td><input type='number' min='0' name='stay__{html.escape(p)}' value='{stay.get(p,1)}'></td></tr>"
        for p in ports
    )

    body = f"""
<section class='card'>
<h2>Техническая страница</h2>
<p>Задайте сутки перехода между каждым портом и базовую стоянку по портам.</p>
<form method='post' action='/technical/save'>
<h3>Матрица переходов</h3>
<div class='table-wrap'>
<table><thead><tr><th>Из \\ В</th>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table>
</div>
<h3>Базовая стоянка</h3>
<table style='max-width:560px;'><thead><tr><th>Порт</th><th>Суток стоянки</th></tr></thead><tbody>{stay_rows}</tbody></table>
<button class='primary' type='submit'>Сохранить настройки</button>
</form>
</section>
"""
    flash = f"<div class='flash {'err' if is_error else ''}'>{html.escape(flash_text)}</div>" if flash_text else ""
    return layout("Техническая страница", body, flash)


def create_handler(service: PlannerService):
    class PlannerHandler(BaseHTTPRequestHandler):
        def _send_html(self, body: str, status: int = 200):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, path: str):
            self.send_response(303)
            self.send_header("Location", path)
            self.end_headers()

        def _read_form(self) -> Dict[str, List[str]]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            return parse_qs(raw, keep_blank_values=True)

        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)

            if parsed.path == "/":
                selected_id = None
                plans = service.list_plans()
                if "plan_id" in query:
                    selected_id = int(query["plan_id"][0])
                elif plans:
                    selected_id = plans[-1]["id"]
                self._send_html(render_index(service, selected_id))
                return

            if parsed.path == "/technical":
                self._send_html(render_technical(service))
                return

            if parsed.path.startswith("/plans/") and parsed.path.endswith("/export"):
                plan_id = int(parsed.path.split("/")[2])
                csv_data = service.export_plan_csv(plan_id).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", f"attachment; filename=plan_{plan_id}.csv")
                self.send_header("Content-Length", str(len(csv_data)))
                self.end_headers()
                self.wfile.write(csv_data)
                return

            if parsed.path.startswith("/plans/") and parsed.path.endswith("/export-html"):
                plan_id = int(parsed.path.split("/")[2])
                html_data = service.export_plan_html(plan_id).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Disposition", f"attachment; filename=plan_{plan_id}.html")
                self.send_header("Content-Length", str(len(html_data)))
                self.end_headers()
                self.wfile.write(html_data)
                return

            self._send_html(layout("404", "<section class='card'><h2>Страница не найдена</h2></section>"), status=404)

        def do_POST(self):
            try:
                if self.path == "/plans":
                    form = self._read_form()
                    ship = form.get("ship", [""])[0]
                    start_date = form.get("start_date", [""])[0]
                    route = [r.strip() for r in form.get("route", [""])[0].split("||") if r.strip()]
                    plan = service.create_plan(ship=ship, route=route, start_date=start_date)
                    self._redirect(f"/?plan_id={plan['id']}")
                    return

                if self.path == "/plans/delete":
                    form = self._read_form()
                    plan_id_raw = form.get("plan_id", [""])[0]
                    if not plan_id_raw:
                        raise ValueError("Выберите план для удаления")

                    plan_id = int(plan_id_raw)
                    service.delete_plan(plan_id)
                    remaining = service.list_plans()
                    redirect_path = "/"
                    if remaining:
                        redirect_path += "?" + urlencode({"plan_id": remaining[-1]["id"]})
                    self._redirect(redirect_path)
                    return

                if self.path.startswith("/plans/") and self.path.endswith("/update"):
                    form = self._read_form()
                    plan_id = int(self.path.split("/")[2])
                    plan = service.get_plan(plan_id)
                    manual_map = {}
                    for idx in range(len(plan["stops"])):
                        arr_key = f"stop_{idx}_arrival"
                        dep_key = f"stop_{idx}_departure"
                        if arr_key in form and dep_key in form:
                            arr = form[arr_key][0]
                            dep = form[dep_key][0]
                            if (arr and not dep) or (dep and not arr):
                                raise ValueError("Заполните обе даты для захода либо очистите обе, чтобы пропустить порт")
                            if arr and dep:
                                service._parse_date(arr)
                                service._parse_date(dep)
                            manual_map[idx] = (arr, dep)
                    frozen_rows = []
                    route_len = len(plan["route"])
                    max_row = (len(plan["stops"]) + route_len - 1) // route_len
                    for row_idx in range(max_row):
                        if f"freeze_row_{row_idx}" in form:
                            frozen_rows.append(row_idx)
                    service.set_frozen_rows(plan_id, frozen_rows)
                    service.update_plan_from_manual_table(plan_id, manual_map)
                    self._send_html(render_index(service, plan_id, "Расписание обновлено с учетом ручных правок"))
                    return

                if self.path.startswith("/plans/") and self.path.endswith("/clear"):
                    plan_id = int(self.path.split("/")[2])
                    service.clear_plan_schedule(plan_id)
                    self._send_html(render_index(service, plan_id, "Расписание очищено"))
                    return

                if self.path == "/technical/save":
                    form = self._read_form()
                    for from_port in service.list_ports():
                        for to_port in service.list_ports():
                            key = f"tr__{from_port}__{to_port}"
                            if key in form:
                                service.set_transition(from_port, to_port, int(form[key][0]))
                    for port in service.list_ports():
                        key = f"stay__{port}"
                        if key in form:
                            service.set_stay(port, int(form[key][0]))
                    self._send_html(render_technical(service, "Технические настройки сохранены"))
                    return

                self._send_html(layout("404", "<section class='card'><h2>Страница не найдена</h2></section>"), status=404)
            except Exception as exc:  # noqa: BLE001
                if self.path.startswith("/technical"):
                    self._send_html(render_technical(service, str(exc), is_error=True), status=400)
                else:
                    selected_id = None
                    if self.path.startswith("/plans/"):
                        try:
                            selected_id = int(self.path.split("/")[2])
                        except Exception:  # noqa: BLE001
                            selected_id = None
                    self._send_html(render_index(service, selected_id, str(exc), is_error=True), status=400)

    return PlannerHandler


def run_server(host: str, port: int, service: PlannerService) -> None:
    server = ThreadingHTTPServer((host, port), create_handler(service))
    print(f"Server running on http://{host}:{port}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Веб-планер морских судов")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    service = PlannerService(PlannerDB())
    run_server(args.host, args.port, service)


if __name__ == "__main__":
    main()
