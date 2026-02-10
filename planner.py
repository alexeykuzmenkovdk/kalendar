#!/usr/bin/env python3
"""Планер заходов морских судов с GUI и экспортом в табличный формат."""

from __future__ import annotations

import argparse
import csv
import html
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except Exception:  # noqa: BLE001
    tk = None
    filedialog = None
    messagebox = None
    ttk = None

DATE_FMT = "%Y-%m-%d"
DEFAULT_DB = Path("planner_db.json")
HALF_YEAR_DAYS = 182
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


@dataclass
class Plan:
    id: int
    ship: str
    route: List[str]
    start_date: str
    end_date: str
    stops: List[Stop]


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

    def create_plan(self, ship: str, route: List[str], start_date: str) -> Dict:
        if ship not in self.db.data["ships"]:
            raise ValueError(f"Судно не найдено: {ship}")
        if len(route) < 2:
            raise ValueError("В маршруте должно быть минимум 2 порта")
        for p in route:
            if p not in self.db.data["ports"]:
                raise ValueError(f"Неизвестный порт: {p}")

        start = self._parse_date(start_date)
        limit = start + timedelta(days=HALF_YEAR_DAYS)
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
            stops.append(Stop(port=port, arrival=self._fmt_date(arrival), departure=self._fmt_date(departure)))
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
        )
        plan_dict = asdict(plan)
        self.db.data["plans"].append(plan_dict)
        self.db.save()
        return plan_dict

    def get_plan(self, plan_id: int) -> Dict:
        for plan in self.db.data["plans"]:
            if plan["id"] == plan_id:
                return plan
        raise ValueError(f"План #{plan_id} не найден")

    def _apply_shift(self, plan: Dict, start_index: int, shift_days: int) -> None:
        for i in range(start_index, len(plan["stops"])):
            arr = self._parse_date(plan["stops"][i]["arrival"]) + timedelta(days=shift_days)
            dep = self._parse_date(plan["stops"][i]["departure"]) + timedelta(days=shift_days)
            plan["stops"][i]["arrival"] = self._fmt_date(arr)
            plan["stops"][i]["departure"] = self._fmt_date(dep)

    def adjust_arrival(self, plan_id: int, stop_index: int, new_arrival: str) -> Dict:
        plan = self.get_plan(plan_id)
        if stop_index < 0 or stop_index >= len(plan["stops"]):
            raise ValueError("Неверный индекс остановки")

        old_arrival = self._parse_date(plan["stops"][stop_index]["arrival"])
        target_arrival = self._parse_date(new_arrival)
        shift_days = (target_arrival - old_arrival).days
        if shift_days != 0:
            self._apply_shift(plan, stop_index, shift_days)
            self.db.save()
        return plan

    def manual_update_stop(
        self,
        plan_id: int,
        stop_index: int,
        arrival: Optional[str] = None,
        departure: Optional[str] = None,
        propagate: bool = False,
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

        if self._parse_date(stop["departure"]) < self._parse_date(stop["arrival"]):
            raise ValueError("Дата отхода не может быть раньше прибытия")

        if propagate:
            new_arrival = self._parse_date(stop["arrival"])
            new_departure = self._parse_date(stop["departure"])
            shift_days = (new_departure - old_departure).days
            if shift_days == 0:
                shift_days = (new_arrival - old_arrival).days
            if shift_days != 0:
                self._apply_shift(plan, stop_index + 1, shift_days)

        self.db.save()
        return plan

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

    def build_schedule_matrix(self, plan_id: int, placeholder: str = PLACEHOLDER) -> Dict:
        plan = self.get_plan(plan_id)
        route = plan["route"]
        port_columns: List[str] = []
        for port in route:
            if port not in port_columns:
                port_columns.append(port)

        rows_count = (len(plan["stops"]) + len(route) - 1) // len(route)
        rows: List[Dict[str, str]] = []
        for _ in range(rows_count):
            row = {}
            for port in port_columns:
                row[f"{port}__arrival"] = placeholder
                row[f"{port}__departure"] = placeholder
            rows.append(row)

        for idx, stop in enumerate(plan["stops"]):
            row_idx = idx // len(route)
            port = stop["port"]
            if port in port_columns:
                rows[row_idx][f"{port}__arrival"] = stop["arrival"]
                rows[row_idx][f"{port}__departure"] = stop["departure"]

        return {"plan": plan, "ports": port_columns, "rows": rows, "placeholder": placeholder}

    def export_plan_csv(self, plan_id: int, output_path: Path, placeholder: str = PLACEHOLDER) -> Path:
        matrix = self.build_schedule_matrix(plan_id=plan_id, placeholder=placeholder)
        ports = matrix["ports"]
        rows = matrix["rows"]

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            first_header = []
            second_header = []
            for port in ports:
                first_header.extend([port, ""])
                second_header.extend(["Приход", "Отход"])
            writer.writerow(first_header)
            writer.writerow(second_header)

            for row in rows:
                line = []
                for port in ports:
                    line.extend([row[f"{port}__arrival"], row[f"{port}__departure"]])
                writer.writerow(line)

        return output_path

    def export_plan_html(self, plan_id: int, output_path: Path, placeholder: str = PLACEHOLDER) -> Path:
        matrix = self.build_schedule_matrix(plan_id=plan_id, placeholder=placeholder)
        plan = matrix["plan"]
        ports = matrix["ports"]
        rows = matrix["rows"]

        title = f"{plan['ship']} ({plan['start_date']} — {plan['end_date']})"
        cols = len(ports) * 2

        def esc(text: str) -> str:
            return html.escape(text)

        html_rows = []
        for row in rows:
            tds = []
            for port in ports:
                tds.append(f"<td>{esc(row[f'{port}__arrival'])}</td>")
                tds.append(f"<td>{esc(row[f'{port}__departure'])}</td>")
            html_rows.append(f"<tr>{''.join(tds)}</tr>")

        port_header = "".join(f"<th colspan='2'>{esc(port)}</th>" for port in ports)
        move_header = "".join("<th>Приход</th><th>Отход</th>" for _ in ports)

        doc = f"""<!doctype html>
<html lang='ru'>
<head>
<meta charset='utf-8'>
<title>{esc(title)}</title>
<style>
body {{ font-family: 'Times New Roman', serif; margin: 20px; }}
table {{ border-collapse: collapse; width: 100%; table-layout: fixed; }}
th, td {{ border: 1px solid #000; padding: 4px; font-size: 12px; text-align: center; }}
.title {{ font-size: 22px; font-style: italic; text-align: left; }}
.port {{ font-size: 13px; }}
</style>
</head>
<body>
<table>
<tr><th class='title' colspan='{cols}'>{esc(title)}</th></tr>
<tr>{port_header}</tr>
<tr>{move_header}</tr>
{''.join(html_rows)}
</table>
</body>
</html>
"""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(doc, encoding="utf-8")
        return output_path


def print_plan(plan: Dict) -> None:
    print(f"\nПлан #{plan['id']} для {plan['ship']}")
    print(f"Период: {plan['start_date']} — {plan['end_date']}")
    print("Маршрут:", " -> ".join(plan["route"]))
    print("\nОстановки:")
    print("idx | порт | прибытие | отход")
    print("-" * 70)
    for i, stop in enumerate(plan["stops"]):
        print(f"{i:>3} | {stop['port']:<30} | {stop['arrival']} | {stop['departure']}")


def run_cli_menu(service: PlannerService) -> None:
    while True:
        print(
            """
=== Планер морских судов ===
1. Список судов
2. Список портов
3. Создать план на полгода
4. Показать план
5. Сдвинуть дату прибытия
6. Ручное изменение остановки
7. Технический раздел
8. Список всех планов
9. Экспорт плана в HTML + CSV
0. Выход
"""
        )
        cmd = input("Выберите пункт: ").strip()

        try:
            if cmd == "1":
                for s in service.list_ships():
                    print(f"- {s}")
            elif cmd == "2":
                for p in service.list_ports():
                    print(f"- {p}")
            elif cmd == "3":
                ship = input("Судно: ").strip()
                route = [r.strip() for r in input("Маршрут через запятую: ").split(",") if r.strip()]
                start_date = input("Дата старта (YYYY-MM-DD): ").strip()
                print_plan(service.create_plan(ship=ship, route=route, start_date=start_date))
            elif cmd == "4":
                print_plan(service.get_plan(int(input("ID плана: ").strip())))
            elif cmd == "5":
                plan = service.adjust_arrival(
                    int(input("ID плана: ").strip()),
                    int(input("Индекс остановки: ").strip()),
                    input("Новая дата прибытия (YYYY-MM-DD): ").strip(),
                )
                print_plan(plan)
            elif cmd == "6":
                plan = service.manual_update_stop(
                    int(input("ID плана: ").strip()),
                    int(input("Индекс остановки: ").strip()),
                    input("Новая дата прибытия (или Enter): ").strip() or None,
                    input("Новая дата отхода (или Enter): ").strip() or None,
                    input("Сдвигать последующие даты? (y/n): ").strip().lower() == "y",
                )
                print_plan(plan)
            elif cmd == "7":
                sub = input("1) Переход 2) Стоянка: ").strip()
                if sub == "1":
                    service.set_transition(
                        input("Откуда: ").strip(), input("Куда: ").strip(), int(input("Суток: ").strip())
                    )
                else:
                    service.set_stay(input("Порт: ").strip(), int(input("Суток: ").strip()))
                print("Сохранено")
            elif cmd == "8":
                for p in service.list_plans():
                    print(f"#{p['id']} | {p['ship']} | {p['start_date']} — {p['end_date']}")
            elif cmd == "9":
                pid = int(input("ID плана: ").strip())
                base = Path(input("Путь без расширения (например exports/plan_1): ").strip())
                html_path = service.export_plan_html(pid, base.with_suffix(".html"))
                csv_path = service.export_plan_csv(pid, base.with_suffix(".csv"))
                print(f"Экспортировано:\n- {html_path}\n- {csv_path}")
            elif cmd == "0":
                break
            else:
                print("Неизвестная команда")
        except Exception as exc:  # noqa: BLE001
            print(f"Ошибка: {exc}")


class PlannerApp:
    def __init__(self, service: PlannerService) -> None:
        if tk is None or ttk is None:
            raise RuntimeError("Tkinter недоступен в текущем окружении")
        self.service = service
        self.root = tk.Tk()
        self.root.title("Планер морских судов")
        self.root.geometry("1300x780")

        self.selected_plan_id: Optional[int] = None
        self.current_route: List[str] = []

        self._build_ui()
        self._refresh_plan_selector()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(frame)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Судно").grid(row=0, column=0, sticky="w")
        self.ship_combo = ttk.Combobox(top, values=self.service.list_ships(), width=35, state="readonly")
        self.ship_combo.grid(row=1, column=0, padx=5)
        self.ship_combo.set(self.service.list_ships()[0])

        ttk.Label(top, text="Дата старта (YYYY-MM-DD)").grid(row=0, column=1, sticky="w")
        self.start_entry = ttk.Entry(top, width=18)
        self.start_entry.grid(row=1, column=1, padx=5)
        self.start_entry.insert(0, datetime.now().strftime(DATE_FMT))

        ttk.Label(top, text="Добавить порт в маршрут").grid(row=0, column=2, sticky="w")
        self.port_combo = ttk.Combobox(top, values=self.service.list_ports(), width=35, state="readonly")
        self.port_combo.grid(row=1, column=2, padx=5)
        self.port_combo.set(self.service.list_ports()[0])

        ttk.Button(top, text="+ Добавить", command=self._add_route_port).grid(row=1, column=3, padx=5)
        ttk.Button(top, text="- Удалить", command=self._remove_route_port).grid(row=1, column=4, padx=5)
        ttk.Button(top, text="Создать план", command=self._create_plan).grid(row=1, column=5, padx=5)

        route_frame = ttk.LabelFrame(frame, text="Маршрут")
        route_frame.pack(fill=tk.X, pady=(10, 8))
        self.route_list = tk.Listbox(route_frame, height=3)
        self.route_list.pack(fill=tk.X, padx=6, pady=6)

        plan_bar = ttk.Frame(frame)
        plan_bar.pack(fill=tk.X, pady=8)
        ttk.Label(plan_bar, text="План").pack(side=tk.LEFT)
        self.plan_combo = ttk.Combobox(plan_bar, state="readonly", width=70)
        self.plan_combo.pack(side=tk.LEFT, padx=6)
        self.plan_combo.bind("<<ComboboxSelected>>", lambda _: self._load_selected_plan())

        ttk.Button(plan_bar, text="Обновить", command=self._load_selected_plan).pack(side=tk.LEFT, padx=4)

        edit_bar = ttk.Frame(frame)
        edit_bar.pack(fill=tk.X, pady=(4, 8))

        ttk.Label(edit_bar, text="Индекс").grid(row=0, column=0)
        self.stop_idx = ttk.Entry(edit_bar, width=6)
        self.stop_idx.grid(row=1, column=0, padx=4)
        ttk.Label(edit_bar, text="Прибытие").grid(row=0, column=1)
        self.arr_entry = ttk.Entry(edit_bar, width=14)
        self.arr_entry.grid(row=1, column=1, padx=4)
        ttk.Label(edit_bar, text="Отход").grid(row=0, column=2)
        self.dep_entry = ttk.Entry(edit_bar, width=14)
        self.dep_entry.grid(row=1, column=2, padx=4)

        self.propagate_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(edit_bar, text="Сдвигать следующие", variable=self.propagate_var).grid(row=1, column=3, padx=6)
        ttk.Button(edit_bar, text="Ручное обновление", command=self._manual_update).grid(row=1, column=4, padx=4)
        ttk.Button(edit_bar, text="Сдвиг по прибытия", command=self._adjust_arrival).grid(row=1, column=5, padx=4)

        tech = ttk.LabelFrame(frame, text="Техническая страница")
        tech.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(tech, text="Переход:").grid(row=0, column=0, padx=4)
        self.tr_from = ttk.Combobox(tech, values=self.service.list_ports(), width=25, state="readonly")
        self.tr_to = ttk.Combobox(tech, values=self.service.list_ports(), width=25, state="readonly")
        self.tr_days = ttk.Entry(tech, width=8)
        self.tr_from.grid(row=0, column=1)
        self.tr_to.grid(row=0, column=2)
        self.tr_days.grid(row=0, column=3)
        self.tr_from.set(self.service.list_ports()[0])
        self.tr_to.set(self.service.list_ports()[1])
        self.tr_days.insert(0, "2")
        ttk.Button(tech, text="Сохранить переход", command=self._save_transition).grid(row=0, column=4, padx=6)

        ttk.Label(tech, text="Стоянка:").grid(row=1, column=0, padx=4)
        self.st_port = ttk.Combobox(tech, values=self.service.list_ports(), width=25, state="readonly")
        self.st_days = ttk.Entry(tech, width=8)
        self.st_port.grid(row=1, column=1)
        self.st_days.grid(row=1, column=2)
        self.st_port.set(self.service.list_ports()[0])
        self.st_days.insert(0, "1")
        ttk.Button(tech, text="Сохранить стоянку", command=self._save_stay).grid(row=1, column=4, padx=6)

        export_bar = ttk.Frame(frame)
        export_bar.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(export_bar, text="Экспорт HTML/CSV", command=self._export_plan).pack(side=tk.LEFT)

        columns = ("idx", "port", "arrival", "departure")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", height=15)
        for col, title, width in [
            ("idx", "#", 50),
            ("port", "Порт", 450),
            ("arrival", "Приход", 120),
            ("departure", "Отход", 120),
        ]:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor="center")
        self.tree.pack(fill=tk.BOTH, expand=True)

    def _show_error(self, text: str) -> None:
        if messagebox:
            messagebox.showerror("Ошибка", text)

    def _show_info(self, text: str) -> None:
        if messagebox:
            messagebox.showinfo("Готово", text)

    def _add_route_port(self) -> None:
        port = self.port_combo.get().strip()
        if port:
            self.current_route.append(port)
            self.route_list.insert(tk.END, port)

    def _remove_route_port(self) -> None:
        sel = self.route_list.curselection()
        if not sel:
            return
        idx = sel[0]
        self.route_list.delete(idx)
        self.current_route.pop(idx)

    def _create_plan(self) -> None:
        try:
            plan = self.service.create_plan(
                ship=self.ship_combo.get().strip(),
                route=self.current_route,
                start_date=self.start_entry.get().strip(),
            )
            self._refresh_plan_selector(selected=plan["id"])
            self._fill_stops(plan)
            self._show_info(f"План #{plan['id']} создан")
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))

    def _refresh_plan_selector(self, selected: Optional[int] = None) -> None:
        plans = self.service.list_plans()
        values = [f"{p['id']} | {p['ship']} | {p['start_date']} — {p['end_date']}" for p in plans]
        self.plan_combo["values"] = values
        if not plans:
            self.selected_plan_id = None
            return
        if selected is None:
            selected = plans[-1]["id"]
        for i, p in enumerate(plans):
            if p["id"] == selected:
                self.plan_combo.current(i)
                break
        self._load_selected_plan()

    def _get_selected_plan_id_from_combo(self) -> Optional[int]:
        text = self.plan_combo.get().strip()
        if not text:
            return None
        return int(text.split("|", 1)[0].strip())

    def _load_selected_plan(self) -> None:
        pid = self._get_selected_plan_id_from_combo()
        if pid is None:
            return
        try:
            plan = self.service.get_plan(pid)
            self.selected_plan_id = pid
            self._fill_stops(plan)
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))

    def _fill_stops(self, plan: Dict) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for idx, stop in enumerate(plan["stops"]):
            self.tree.insert("", tk.END, values=(idx, stop["port"], stop["arrival"], stop["departure"]))

    def _manual_update(self) -> None:
        if self.selected_plan_id is None:
            self._show_error("Выберите план")
            return
        try:
            plan = self.service.manual_update_stop(
                plan_id=self.selected_plan_id,
                stop_index=int(self.stop_idx.get().strip()),
                arrival=self.arr_entry.get().strip() or None,
                departure=self.dep_entry.get().strip() or None,
                propagate=self.propagate_var.get(),
            )
            self._fill_stops(plan)
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))

    def _adjust_arrival(self) -> None:
        if self.selected_plan_id is None:
            self._show_error("Выберите план")
            return
        try:
            plan = self.service.adjust_arrival(
                plan_id=self.selected_plan_id,
                stop_index=int(self.stop_idx.get().strip()),
                new_arrival=self.arr_entry.get().strip(),
            )
            self._fill_stops(plan)
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))

    def _save_transition(self) -> None:
        try:
            self.service.set_transition(self.tr_from.get().strip(), self.tr_to.get().strip(), int(self.tr_days.get()))
            self._show_info("Переход сохранен")
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))

    def _save_stay(self) -> None:
        try:
            self.service.set_stay(self.st_port.get().strip(), int(self.st_days.get()))
            self._show_info("Стоянка сохранена")
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))

    def _export_plan(self) -> None:
        if self.selected_plan_id is None:
            self._show_error("Выберите план")
            return
        try:
            if filedialog:
                path = filedialog.asksaveasfilename(
                    title="Экспорт расписания",
                    defaultextension=".html",
                    filetypes=(("HTML", "*.html"),),
                )
            else:
                path = ""
            if not path:
                return
            html_path = Path(path)
            csv_path = html_path.with_suffix(".csv")
            self.service.export_plan_html(self.selected_plan_id, html_path)
            self.service.export_plan_csv(self.selected_plan_id, csv_path)
            self._show_info(f"Экспортировано:\n{html_path}\n{csv_path}")
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Планер заходов морских судов")
    parser.add_argument("--cli", action="store_true", help="Запустить консольный режим вместо GUI")
    args = parser.parse_args()

    service = PlannerService(PlannerDB())
    if args.cli or tk is None:
        run_cli_menu(service)
    else:
        PlannerApp(service).run()


if __name__ == "__main__":
    main()
