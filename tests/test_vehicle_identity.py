import unittest

from app.meet_response_worker_v2 import (
    looks_like_driver_name,
    looks_like_vehicle_number,
    normalize_driver_name,
    normalize_vehicle_number,
)


class VehicleIdentityTests(unittest.TestCase):
    def test_normalizes_full_width_vehicle_number(self):
        self.assertTrue(looks_like_vehicle_number("車番は１２ー３４です"))
        self.assertEqual(normalize_vehicle_number("車番は１２ー３４です"), "12-34")
        self.assertEqual(normalize_vehicle_number("車番は いち に の さん よん です"), "12-34")
        self.assertEqual(normalize_vehicle_number("車番は一二三四です"), "1234")

    def test_rejects_name_as_vehicle_number(self):
        self.assertFalse(looks_like_vehicle_number("山田太郎です"))

    def test_normalizes_spoken_driver_name(self):
        self.assertTrue(looks_like_driver_name("名前は山田太郎です"))
        self.assertEqual(
            normalize_driver_name("名前は山田太郎です。"),
            "山田太郎",
        )


if __name__ == "__main__":
    unittest.main()
