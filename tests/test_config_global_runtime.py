from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from service_check.config import load_config, render_effective_config, validate_config


class GlobalRuntimeConfigTest(unittest.TestCase):
    def test_loads_global_runtime_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "service-check.ini"
            config_path.write_text(
                "\n".join(
                    [
                        "[global]",
                        "log_level=debug",
                        "show_results=1",
                    ]
                ),
                encoding="utf-8",
            )

            loaded = load_config(str(config_path))

        self.assertEqual(loaded.global_config.log_level, "DEBUG")
        self.assertTrue(loaded.global_config.show_results)

    def test_render_effective_config_includes_runtime_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "service-check.ini"
            config_path.write_text("[global]\nlog_level=WARNING\nshow_results=0\n", encoding="utf-8")

            rendered = render_effective_config(load_config(str(config_path)))

        self.assertIn("log_level=WARNING", rendered)
        self.assertIn("show_results=0", rendered)

    def test_invalid_log_level_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "service-check.ini"
            config_path.write_text("[global]\nlog_level=noisy\n", encoding="utf-8")

            issues = validate_config(str(config_path))

        self.assertEqual(issues, ["[global] key log_level must be one of: CRITICAL, DEBUG, ERROR, INFO, WARNING"])


if __name__ == "__main__":
    unittest.main()
