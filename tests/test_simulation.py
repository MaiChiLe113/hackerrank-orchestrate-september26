import unittest
from datetime import date, timedelta
from decimal import Decimal as D
from buy_wait.schemas import EngineConfig, FinancialProfile, LedgerEntry, Payment, Request
from buy_wait.simulation import earliest_full_date, safe_amount, simulate

class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 1, 1)
        self.request = Request("r", "u", self.day, "purchase", D("100"), self.day + timedelta(days=30), True)
        self.profile = FinancialProfile("u", "USD", D("200"), D("100"))

    def test_future_shortfall_limits_immediate_payment(self):
        entries = [LedgerEntry(self.day + timedelta(days=3), D("-80"), "bill", "rent"), LedgerEntry(self.day + timedelta(days=5), D("200"), "salary", "salary")]
        self.assertEqual(safe_amount(self.request, self.profile, entries), D("20.00"))
        self.assertEqual(earliest_full_date(self.request, self.profile, entries), self.day + timedelta(days=5))
        self.assertFalse(simulate(self.request, self.profile, entries, [Payment(self.day, D("20.01"))]).safe)

    def test_same_day_income_is_explicit_policy(self):
        entries = [LedgerEntry(self.day, D("100"), "salary", "salary"), LedgerEntry(self.day, D("-80"), "bill", "rent")]
        self.assertEqual(safe_amount(self.request, self.profile, entries), D("100.00"))
        self.assertEqual(safe_amount(self.request, self.profile, entries, EngineConfig(same_day_order="debits_first")), D("20.00"))

    def test_pending_hold_cannot_borrow_from_later_income(self):
        entries = [LedgerEntry(self.day, D("-110"), "pending", "rent", kind="reserve"), LedgerEntry(self.day, D("500"), "salary", "salary")]
        self.assertFalse(simulate(self.request, self.profile, entries).safe)
        self.assertEqual(safe_amount(self.request, self.profile, entries), D("0"))

    def test_horizon_includes_day_90(self):
        entries = [LedgerEntry(self.day + timedelta(days=90), D("-70"), "bill", "rent")]
        self.assertEqual(safe_amount(self.request, self.profile, entries), D("30.00"))

    def test_exact_reserve_boundary_is_safe(self):
        self.assertTrue(simulate(self.request, self.profile, [], [Payment(self.day, D("100"))]).safe)
        self.assertEqual(earliest_full_date(self.request, self.profile, []), self.day)

    def test_reject_out_of_horizon_payment(self):
        with self.assertRaises(ValueError):
            simulate(self.request, self.profile, [], [Payment(self.day + timedelta(days=91), D("1"))])

if __name__ == "__main__":
    unittest.main()
