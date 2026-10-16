import sys
import unittest
import pytest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "workers"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import handlers as h
from ledger import connect


class Flow(unittest.TestCase):
    def setUp(self):
        self.con = connect(":memory:")

    @pytest.mark.story("POL-3")

    @pytest.mark.story("CLM-1")

    @pytest.mark.story("CLM-5")

    def test_policy_to_paid_to_claim_to_payout(self):
        v = dict(applicant="A", product="term_life", age=34, sumInsured=5_000_000)
        v["age"] = 34
        self.assertTrue(h.validate_application(self.con, v)["valid"])
        v.update(h.calculate_premium(self.con, {**v, "loadingPct": 10}))
        self.assertGreater(v["premium"], 0)                                   # actuarial gross premium
        smoker = h.calculate_premium(self.con, {**v, "mortalityMultiple": 1.8, "age": 34, "loadingPct": 0})["premium"]
        base = h.calculate_premium(self.con, {**v, "mortalityMultiple": 1.0, "age": 34, "loadingPct": 0})["premium"]
        self.assertGreater(smoker, base * 1.5)
        v.update(h.issue_policy(self.con, v))
        h.create_invoice(self.con, v)
        self.assertEqual(h.verify_coverage(self.con, dict(policyNumber=v["policyNumber"], claimAmount=100))["covered"], False)  # not yet paid
        h.reconcile_payment(self.con, {**v, "amountPaid": v["premium"], "paymentRef": "X"})
        c = h.verify_coverage(self.con, dict(policyNumber=v["policyNumber"], claimAmount=8000))
        self.assertTrue(c["covered"])
        cv = dict(policyNumber=v["policyNumber"], claimAmount=8000, **c)
        h.settle_claim(self.con, cv)
        self.assertEqual(self.con.execute("SELECT status FROM payments WHERE kind='payout'").fetchone()[0], "pending")
        h.close_claim(self.con, {**cv, "payoutRef": "N1"})
        self.assertEqual(self.con.execute("SELECT status FROM payments WHERE kind='payout'").fetchone()[0], "paid")

    @pytest.mark.story("POL-4")

    def test_lapse_and_validation(self):
        self.assertFalse(h.validate_application(self.con, dict(applicant="A", product="x", age=3, sumInsured=1))["valid"])
        v = dict(applicant="A", product="motor", age=40, sumInsured=100, premium=3.5)
        v.update(h.issue_policy(self.con, v))
        h.lapse_policy(self.con, v)
        self.assertEqual(self.con.execute("SELECT status FROM policies").fetchone()[0], "lapsed")

    @pytest.mark.story("POL-3")

    def test_partial_payment_keeps_policy_pending(self):
        v = dict(applicant="A", product="motor", age=40, sumInsured=1000, premium=35.0)
        v.update(h.issue_policy(self.con, v)); h.create_invoice(self.con, v)
        h.reconcile_payment(self.con, {**v, "amountPaid": 10.0})
        self.assertEqual(self.con.execute("SELECT status FROM policies").fetchone()[0], "pending_payment")


if __name__ == "__main__":
    unittest.main()
