import tempfile
import unittest
from pathlib import Path

from planner import PlannerDB, PlannerService, render_index, render_technical


class PlannerServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test_db.json"
        self.service = PlannerService(PlannerDB(self.db_path))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_create_plan_generates_stops(self):
        plan = self.service.create_plan(
            ship="т/х «Анатолий Иванов»",
            route=["Владивосток", "Корсаков (о. Сахалин)"],
            start_date="2026-01-01",
        )
        self.assertGreater(len(plan["stops"]), 2)
        self.assertEqual(plan["stops"][0]["arrival"], "2026-01-01")

    def test_update_schedule_from_manual_table_shifts_following(self):
        plan = self.service.create_plan(
            ship="т/х «Ерофей Хабаров»",
            route=["Владивосток", "Славянка", "Невельск"],
            start_date="2026-02-01",
        )
        initial_third_arrival = plan["stops"][2]["arrival"]

        self.service.update_plan_from_manual_table(
            plan["id"],
            {1: ("2026-02-08", "2026-02-09")},
        )

        updated = self.service.get_plan(plan["id"])
        self.assertEqual(updated["stops"][1]["arrival"], "2026-02-08")
        self.assertNotEqual(updated["stops"][2]["arrival"], initial_third_arrival)

    def test_full_manual_form_recalculates_unedited_stops(self):
        plan = self.service.create_plan(
            ship="т/х «Ерофей Хабаров»",
            route=["Владивосток", "Славянка", "Невельск"],
            start_date="2026-02-01",
        )
        initial_third_arrival = plan["stops"][2]["arrival"]

        manual_map = {
            idx: (stop["arrival"], stop["departure"])
            for idx, stop in enumerate(plan["stops"])
        }
        manual_map[0] = (plan["stops"][0]["arrival"], "2026-03-02")

        self.service.update_plan_from_manual_table(plan["id"], manual_map)

        updated = self.service.get_plan(plan["id"])
        self.assertEqual(updated["stops"][0]["departure"], "2026-03-02")
        self.assertNotEqual(updated["stops"][2]["arrival"], initial_third_arrival)

    def test_html_render_contains_schedule_headers(self):
        plan = self.service.create_plan(
            ship="т/х «Русский Восток»",
            route=["Владивосток", "Курильск (о. Итуруп)", "Корсаков (о. Сахалин)"],
            start_date="2026-03-01",
        )
        page = render_index(self.service, selected_id=plan["id"])
        self.assertIn("Приход", page)
        self.assertIn("Отход", page)
        self.assertIn("Обновить расписание", page)
        self.assertIn("Экспорт CSV", page)
        self.assertIn("Экспорт HTML", page)


    def test_manual_table_allows_skipped_port(self):
        plan = self.service.create_plan(
            ship="т/х «Анатолий Иванов»",
            route=["Владивосток", "Славянка", "Невельск"],
            start_date="2026-04-01",
        )

        self.service.update_plan_from_manual_table(
            plan["id"],
            {1: ("", "")},
        )

        updated = self.service.get_plan(plan["id"])
        self.assertTrue(updated["stops"][1]["skipped"])
        self.assertEqual(updated["stops"][1]["arrival"], "")
        self.assertEqual(updated["stops"][1]["departure"], "")


    def test_frozen_period_keeps_initial_segment_unchanged(self):
        plan = self.service.create_plan(
            ship="т/х «Ерофей Хабаров»",
            route=["Владивосток", "Славянка", "Невельск"],
            start_date="2026-02-01",
        )
        frozen_departure = plan["stops"][1]["departure"]
        frozen_arrival = plan["stops"][1]["arrival"]

        self.service.set_frozen_until(plan["id"], frozen_departure)
        self.service.update_plan_from_manual_table(
            plan["id"],
            {
                0: ("2026-03-10", "2026-03-11"),
                3: ("2026-03-15", "2026-03-16"),
            },
        )

        updated = self.service.get_plan(plan["id"])
        self.assertEqual(updated["stops"][1]["arrival"], frozen_arrival)
        self.assertEqual(updated["stops"][1]["departure"], frozen_departure)
        self.assertEqual(updated["stops"][3]["arrival"], "2026-03-15")
        self.assertEqual(updated["frozen_until"], frozen_departure)

    def test_export_plan_csv_contains_header(self):
        plan = self.service.create_plan(
            ship="т/х «Ерофей Хабаров»",
            route=["Владивосток", "Славянка"],
            start_date="2026-05-01",
        )
        csv_text = self.service.export_plan_csv(plan["id"])

        self.assertIn("plan_id,ship,port,arrival,departure,skipped", csv_text)

    def test_export_plan_html_contains_table(self):
        plan = self.service.create_plan(
            ship="т/х «Ерофей Хабаров»",
            route=["Владивосток", "Славянка"],
            start_date="2026-05-01",
        )
        html_text = self.service.export_plan_html(plan["id"])

        self.assertIn("<table>", html_text)
        self.assertIn("<th colspan='2'>Владивосток</th>", html_text)
        self.assertIn("<th>Приход</th><th>Отход</th>", html_text)
        self.assertIn("т/х «Ерофей Хабаров»", html_text)

    def test_delete_plan_removes_it_from_storage(self):
        first = self.service.create_plan(
            ship="т/х «Ерофей Хабаров»",
            route=["Владивосток", "Славянка"],
            start_date="2026-05-01",
        )
        second = self.service.create_plan(
            ship="т/х «Русский Восток»",
            route=["Владивосток", "Невельск"],
            start_date="2026-06-01",
        )

        self.service.delete_plan(first["id"])

        plans = self.service.list_plans()
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["id"], second["id"])
        with self.assertRaises(ValueError):
            self.service.get_plan(first["id"])

    def test_technical_page_contains_matrix_and_stay(self):
        page = render_technical(self.service)
        self.assertIn("Матрица переходов", page)
        self.assertIn("Базовая стоянка", page)


if __name__ == "__main__":
    unittest.main()
