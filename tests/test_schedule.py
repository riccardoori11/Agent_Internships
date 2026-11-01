import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo


PROJECT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("project_schedule", PROJECT / "scripts" / "schedule.py")
schedule = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(schedule)


class ScheduleTests(unittest.TestCase):
    def test_explicit_paris_timezone_and_catchup(self):
        unit = (PROJECT / "deploy" / "internship-finder.timer").read_text()
        self.assertIn("OnCalendar=*-*-* 08:00:00 Europe/Paris", unit)
        self.assertIn("Persistent=true", unit)
        self.assertIn("RandomizedDelaySec=0", unit)

    def test_paris_dst_at_scheduled_hour(self):
        paris = ZoneInfo("Europe/Paris")
        samples = [(2026, 3, 28, 7), (2026, 3, 29, 6), (2026, 10, 24, 6), (2026, 10, 25, 7)]
        for year, month, day, utc_hour in samples:
            with self.subTest(date=(year, month, day)):
                instant = datetime(year, month, day, 8, tzinfo=paris)
                self.assertEqual(instant.astimezone(timezone.utc).hour, utc_hour)

    def test_service_has_absolute_paths_and_escaped_specifiers(self):
        self.assertEqual(schedule.systemd_quote(Path('/tmp/a %b $c "d')), '"/tmp/a %%b $$c \\"d"')
        with tempfile.TemporaryDirectory(prefix="internships with spaces ") as temp:
            project = Path(temp)
            (project / "deploy").mkdir()
            for name in ["internship-finder.service.in", "internship-finder.timer"]:
                (project / "deploy" / name).write_text((PROJECT / "deploy" / name).read_text())
            generated = schedule.generate(project)
            service = (generated / "internship-finder.service").read_text()
            self.assertIn(f'WorkingDirectory="{project}"', service)
            self.assertIn(f'ExecStart="{project}/scripts/run-daily.sh"', service)


if __name__ == "__main__":
    unittest.main()
