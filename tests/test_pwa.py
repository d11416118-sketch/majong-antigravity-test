import json
import struct
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"Not a PNG file: {path}")
    return struct.unpack(">II", data[16:24])


class TestPwaPackaging(unittest.TestCase):
    def test_manifest_icons_exist_at_declared_sizes(self):
        manifest = json.loads((ROOT / "static" / "manifest.webmanifest").read_text(encoding="utf-8"))

        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["orientation"], "any")
        self.assertEqual(manifest["lang"], "zh-TW")
        self.assertTrue(any(icon.get("purpose") == "maskable" for icon in manifest["icons"]))

        for icon in manifest["icons"]:
            relative_path = icon["src"].removeprefix("/")
            icon_path = ROOT / relative_path
            expected_size = tuple(int(value) for value in icon["sizes"].split("x"))
            self.assertTrue(icon_path.is_file(), icon_path)
            self.assertEqual(png_size(icon_path), expected_size)

    def test_html_includes_mobile_install_metadata(self):
        html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

        self.assertIn("viewport-fit=cover", html)
        self.assertIn('rel="manifest"', html)
        self.assertIn('rel="apple-touch-icon"', html)
        self.assertIn("apple-mobile-web-app-title", html)
        self.assertIn('id="turn-status-banner"', html)
        self.assertEqual(png_size(ROOT / "static" / "icons" / "app-icon-32.png"), (32, 32))
        self.assertEqual(png_size(ROOT / "static" / "icons" / "apple-touch-icon.png"), (180, 180))

    def test_service_worker_precaches_install_icons(self):
        worker = (ROOT / "static" / "service-worker.js").read_text(encoding="utf-8")

        for name in (
            "app-icon-192.png",
            "app-icon-512.png",
            "app-icon-maskable-512.png",
            "apple-touch-icon.png",
        ):
            self.assertIn(name, worker)

    def test_fbx_loader_dependencies_are_packaged(self):
        worker = (ROOT / "static" / "service-worker.js").read_text(encoding="utf-8")
        dependencies = (
            "vendor/three/addons/loaders/FBXLoader.js",
            "vendor/three/addons/libs/fflate.module.js",
            "vendor/three/addons/curves/NURBSCurve.js",
            "vendor/three/addons/curves/NURBSUtils.js",
        )

        for relative_path in dependencies:
            self.assertTrue((ROOT / "static" / relative_path).is_file(), relative_path)
            self.assertIn(f"/static/{relative_path}", worker)

    def test_lobby_character_scales_fbx_center_translation(self):
        viewer = (ROOT / "static" / "js" / "lobby-character.js").read_text(encoding="utf-8")

        self.assertIn("model.position.copy(center).multiplyScalar(-scale);", viewer)


if __name__ == "__main__":
    unittest.main()
