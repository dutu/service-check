from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from service_check.doctor import ERROR, run_doctor


class DoctorConfigValidationTest(unittest.TestCase):
    def test_reports_unknown_option_names_in_main_and_dropin_configs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "service-check.ini"
            config_path.write_text("[global]\nshow_reults=1\n", encoding="utf-8")
            dropin_dir = Path(f"{config_path}.d")
            dropin_dir.mkdir()
            (dropin_dir / "10-ping.ini").write_text(
                "[gateway_ping]\nenabled=0\ncheck=ping\nhots=127.0.0.1\n",
                encoding="utf-8",
            )

            with patch("service_check.doctor._check_systemd_units", return_value=[]):
                results = run_doctor(str(config_path))

        errors = [result.message for result in results if result.status == ERROR]
        self.assertIn("[global] unknown key: show_reults; did you mean show_results?", errors)
        self.assertIn("[gateway_ping] unknown key: hots; did you mean host?", errors)


if __name__ == "__main__":
    unittest.main()
