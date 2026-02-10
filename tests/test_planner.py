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

    def test_technical_page_contains_matrix_and_stay(self):
        page = render_technical(self.service)
        self.assertIn("Матрица переходов", page)
        self.assertIn("Базовая стоянка", page)


if __name__ == "__main__":
    unittest.main()
