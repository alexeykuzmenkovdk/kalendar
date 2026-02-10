import tempfile
import unittest
from pathlib import Path

from planner import PLACEHOLDER, PlannerDB, PlannerService


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

    def test_adjust_arrival_shifts_next_stops(self):
        plan = self.service.create_plan(
            ship="т/х «Ерофей Хабаров»",
            route=["Владивосток", "Славянка", "Невельск"],
            start_date="2026-02-01",
        )
        plan_id = plan["id"]
        updated = self.service.adjust_arrival(plan_id, 1, "2026-02-07")
        self.assertEqual(updated["stops"][1]["arrival"], "2026-02-07")

    def test_export_matrix_html_and_csv(self):
        plan = self.service.create_plan(
            ship="т/х «Русский Восток»",
            route=["Владивосток", "Курильск (о. Итуруп)", "Корсаков (о. Сахалин)"],
            start_date="2026-03-01",
        )
        out_html = Path(self.tmpdir.name) / "plan.html"
        out_csv = Path(self.tmpdir.name) / "plan.csv"

        self.service.export_plan_html(plan["id"], out_html)
        self.service.export_plan_csv(plan["id"], out_csv)

        html_content = out_html.read_text(encoding="utf-8")
        csv_content = out_csv.read_text(encoding="utf-8-sig")

        self.assertIn("т/х «Русский Восток»", html_content)
        self.assertIn("Владивосток", html_content)
        self.assertIn(PLACEHOLDER, csv_content)
        self.assertIn("Приход", csv_content)


if __name__ == "__main__":
    unittest.main()
